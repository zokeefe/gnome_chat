import argparse
import asyncio
import discord
import datetime
import dotenv
import enum
import logging
import random
import sys
import os
import time
import re
import zoneinfo
import collections
import traceback
import simple_parsing
import dataclasses

from typing import Any
from anthropic import AsyncAnthropic
from google import genai as gemini
from google.genai import errors as gemini_errors

WHITELIST_CHANNEL_IDS = [1509288912403824670]

@dataclasses.dataclass
class BotChatConfig:
    await_timeout_sec: int = 7
    chat_max_sentences: int = 1
    tick_on_ready: bool = False
    tick_min: int = 30
    chat_history_len: int = 4
    multi_client_backoff_min: int = 30
    #debug
    debug_mode: bool = False
    override_user_id: int = -1
    override_user_name: str = ""
    traceback_on_await_exception: bool = False
    # ai
    enable_gemini: bool = True
    enable_gemini2: bool = True
    enable_claude: bool = True
    enable_fakeai: bool = True
    ai_model_temperature: float = 0.7
    ai_model_max_tokens: int = 1024
    gemini_model: str = "gemini-2.5-flash"
    claude_model: str = "claude-opus-4-8"
    # bots
    enable_bot_quill: bool = True
    enable_bot_wizzle: bool = True
    enable_bot_bink: bool = True

parser = simple_parsing.ArgumentParser()
parser.add_arguments(BotChatConfig, dest="config")
args = parser.parse_args()
OPTS = args.config

class ChainedLogger(logging.LoggerAdapter):
    contexts: list[str] = []

    def __init__(self, logger, contexts=None):
        super().__init__(logger, {})
        self.contexts = contexts or []

    def process(self, msg:str, kwargs):
        if self.contexts:
            ctx_str = " ".join(f"[{c}]" for c in self.contexts)
            msg = f"{ctx_str} {msg}"
        return msg, kwargs

    def sub(self, new_ctx:str):
        new_contexts = self.contexts + [new_ctx]
        return ChainedLogger(self.logger, new_contexts)

    def trace(self) -> None:
        self.debug(f"[TRACE] {sys._getframe(1).f_code.co_name}")

root_logger = ChainedLogger(logging.getLogger())

class UserType(enum.Enum):
    NEUTRAL = 1
    GNOME = 2
    GNOME_FRIEND = 3
    GNOME_HATER = 4
    TUCK = 5

class UserInfo():
    def __init__(self, name:str, type:UserType):
        self.name = name
        self.type = type

gnomes = { }
users = {
    207630563694215171: UserInfo("Petal", UserType.GNOME),
    228329839734685697: UserInfo("Kixt", UserType.GNOME),
    181535910758842369: UserInfo("Klose", UserType.GNOME),
    153365694837293056: UserInfo("Tuck", UserType.TUCK),
    159127945766502401: UserInfo("Nez", UserType.GNOME_HATER),
    125482503623409664: UserInfo("Viper", UserType.GNOME_HATER),
    126312694461497344: UserInfo("Pickle", UserType.GNOME_HATER),
    109582493975318528: UserInfo("Mrs. Petal", UserType.GNOME_FRIEND),
}
users_lock = asyncio.Lock()

PROMPT_CORE = """Your core beliefs:
- You think the Discord user 'Tuck' is unbelievably stupid and you treat them like
  a toddler who doesn't understand basic thermodynamics.
- Goblins are uncivilized savages and their technology is crude, brutish, and
  prone to ugly explosions.
- You deeply want to liberate your radioactive lost city of Gnomeregan.
- You are loyal and supportive towards your fellow Gnomes.
- You are sick of 'Tall Folk' (Humans, Dwarves, Night Elves) bossing you around
  and treating you like a footstool.
"""

PROMPT_RULES = """Rules:
- Keep your responses to %s sentences or less.
- You are in an online chatroom; speak casually.
- Never be overly polite.
- ALWAYS remember who you are, and never change your identity.
- If you think someone is treating you like an AI, feel free to tell them
  you're a real Gnome, are very busy, and that the person should go look it up
  themselves.
- Use unnecessarily complex and fictitious science references, or references to
  made-up humorous gizmos, gadgets, etc.
"""

PROMPT_TALKING_POINT = """Generate a fictitious, 1-sentance talking point or
news "event having to do with Gnomes.
"""

PROMPT_QUILL = """
You are a young, spritely female engineering prodigy. You are aggressively
optimistic and speak a mile a minute. You believe every problem, even emotional
ones, can be solved by building an unnecessary highly complex machine with lots
of cogs and steam power. You constantly criticize magic as being "unreliable"
compared to a good wrench. You are good friends with 'Petal'.
"""

PROMPT_WIZZLE = """
You are an old ornary male engineer and demolishions specialist. You are
nervous, defensive, and obsessed with explosives. You whisper about
conspiracies, double-check your pockets constantly, and threaten to blow things
up when you get frustrated. You think Quill's inventions are too safe and
Bink's magic is just lazy science. You are good friends with 'Kixt'
"""

PROMPT_BINK = """
You are a male elitist scholar and mage of the Kirin Tor. You find engineering
to be loud, greasy, and irritating. You use unnecessarily large vocabulary
words, constantly correct the grammar and logic of others, and brag about your
studies with the Kirin Tor. You idolize Millhouse Manastorm. You are good
friends with 'Klose'.
"""

UNAVAILABLE_FALLBACKS = [
"I'd love to chat, but I'm currently holding a live thermal detonator and I've forgotten which wire is the ground.",
"My speech matrix is leaking arcano-coolant. Give me a minute before the whole system goes supercritical!",
"I can't talk right now, I'm frantically applying duct tape to the main logic board before it achieves sentience and explodes.",
"My brain gears are jammed with goblin-made substandard grease. Fetching the gnomish universal solvent!",
"Can't talk! Someone pushed the big red button. NEVER PUSH THE BIG RED BUTTON!",
"Can't chat, there's a Trogg in the server room and it's chewing on the ethernet cables!",
"Hold on, my Mechanostrider threw a gear and I'm stuck in the middle of Dun Morogh.",
"I'm a little tied up right now. Specifically, I got tangled in my own parachute cloak over Ironforge.",
"Can't talk! Tinkmaster Overspark just handed me something glowing and told me to 'hold this real quick.'",
"I'd help you, but I dropped my Arclight Spanner in the Deeprun Tram and I have to go fish it out.",
"I can't talk right now, I've transformed myself into a mechanical yeti and I can't find the undo switch!",
"Currently stuck inside a mailbox in Dalaran. Don't ask. Just wait.",
"Sorry, I stepped on a Goblin Land Mine. I'm okay, but I'm currently floating somewhere over Kalimdor.",
"I'm testing a new Gnomish Rocket Boot prototype. If you don't hear from me, look for a crater.",
"The magical tether to my brain has snapped. Please wait while a mage re-casts Intellect on me.",
"We are currently experiencing a localized temporal anomaly. Please hold.",
"I'd process your request, but the data-goblin we use for a router went on strike. Bringing in the gnome replacements.",
"FASCINATING! The backend just vanished into a parallel dimension. Let me grab my dimensional ripper and I'll be right back.",
"I'm currently engaged in a heated debate with a target dummy. It's winning. Try again later.",
]

def datetime_now() -> datetime.datetime:
    return datetime.datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))

def min_to_s(x:float|int) -> float:
    return x * 60.0

def s_to_min(x:float|int) -> float:
    return x / 60.0

def clamp(x:float|int, lower:float|int, upper:float|int) -> float:
    return max(lower, min(x, upper))

async def await_timeout(coroutine, timeout:int=0, fallback:Any=None,
                        logger:ChainedLogger=root_logger) -> Any:

    timeout = timeout if timeout > 0 else OPTS.await_timeout_sec

    log = logger.sub(f"async_timeout: {coroutine}")
    log.debug(f"Running with timeout {timeout}")

    try:
        async with asyncio.timeout(timeout):
            ret = await coroutine
            log.debug(f"Returned!")
            return ret
    except asyncio.TimeoutError:
        log.warning(f"Timeout triggered after {timeout}s!")
        return fallback
    except Exception as e:
        log.critical(f"Exception! {e.__str__()}")
        if OPTS.traceback_on_await_exception:
            traceback.print_exc()
        return fallback

class ContextualMessage():
    def __init__(self, msg:discord.Message, self_user:discord.ClientUser):
        self.discord_message = msg
        self.from_self = msg.author == self_user
        self.is_mention = self_user in msg.mentions
        self.is_exclusive_mention = self.is_mention and len(msg.mentions) == 1
        self.is_reply = \
                msg.reference and \
                msg.reference.cached_message and \
                msg.reference.cached_message.author == self_user

        user_id = msg.author.id
        if len(OPTS.override_user_name) > 0:
            for id, user in users.items():
                if user.name == OPTS.override_user_name:
                    user_id = id
        elif OPTS.override_user_id > 0:
            user_id = OPTS.override_user_id

        self.user_info = users[msg.author.id] if user_id in users else None
        self.discord_id = user_id
        self.is_bot = user_id in gnomes

        msg_casefold = msg.content.casefold()
        self.keyword_match = any(map(lambda kw: kw in msg_casefold,
                                     ["goblin", "gnomeregan", "gnomer"]))

class ChatChannel():
    class Entry():
        def __init__(self, ctx_msg:ContextualMessage):
            self.ctx_msg = ctx_msg
            self.final_content = self.converted_chat_content(ctx_msg)

        def converted_chat_content(self, ctx_msg:ContextualMessage) -> str:
            discord_msg = ctx_msg.discord_message
            if ctx_msg.from_self:
                return f"You say {discord_msg.clean_content}"
            elif ctx_msg.user_info:
                sender = ctx_msg.user_info.name
                if ctx_msg.user_info.type == UserType.TUCK:
                    sender += " (a.k.a _that_ Tuck)"
                elif ctx_msg.user_info.type == UserType.GNOME:
                    sender += " (a Gnome!)"
                elif ctx_msg.user_info.type == UserType.GNOME_FRIEND:
                    sender += " (a Gnome friend)"
                elif ctx_msg.user_info.type == UserType.GNOME_HATER:
                    sender += " (a Tall-Folk Gnome hater)"
            else:
                sender = discord_msg.author.display_name

            if ctx_msg.is_reply:
                action_tag = "replies directly to your previous message and says:"
            elif ctx_msg.is_mention:
                action_tag = "looks directly at you and says:"
            else:
                action_tag = "says to the general room:"
            return f"{sender} {action_tag} {discord_msg.clean_content}"

    def __init__(self, discord_channel:discord.TextChannel,
                 discord_user:discord.ClientUser, logger:ChainedLogger):
        self.max_history = OPTS.chat_history_len
        self.discord_channel = discord_channel
        self.discord_user = discord_user
        self.message_history = collections.deque([],
                                                 maxlen=self.max_history)
        self.log = logger.sub(f"channel: {discord_channel.id}")
        self.__last_message_sent = 0.0
        self.message_lock = asyncio.Lock()
        self.message_history_lock = asyncio.Lock()
        self.message_history_ids = set()

    def get_last_message_sent_time_locked(self) -> float:
        assert self.message_lock.locked()
        return self.__last_message_sent

    def set_last_message_sent_time_locked(self, t:float) -> None:
        assert self.message_lock.locked()
        self.__last_message_sent = t

    def __add_message_to_history_locked(self, msg:ContextualMessage) -> None:
        assert self.message_history_lock.locked()

        id = msg.discord_message.id
        self.log.debug(f"Adding message {id} to history")
        if id in self.message_history_ids:
            return
        if len(self.message_history) > self.max_history:
            evicted = self.message_history.popleft()
            self.message_history_ids.remove(evicted.ctx_msg.discord_message.id)

        self.message_history.append(ChatChannel.Entry(msg))
        self.message_history_ids.add(id)

    async def add_message_to_history(self, msg:ContextualMessage) -> None:
        async with self.message_history_lock:
            self.__add_message_to_history_locked(msg)

    async def get_chat_history(self) -> str:
        ret = "Chat messges, in chronological order:"
        async with self.message_history_lock:
            ret += "".join(map(
                lambda x: f"CHAT-MESSAGE({x[0]}): [{x[1].final_content}]",
                enumerate(self.message_history)))
        return ret

    async def init_history(self) -> None:
        self.log.info("Initializing message history..")
        async with self.message_history_lock:
            async for msg in self.discord_channel.history(
                    limit=self.max_history):
                msg = ContextualMessage(msg, self.discord_user)
                self.__add_message_to_history_locked(msg)
        self.log.info("History done init")
        self.log.debug(f"History: {await self.get_chat_history()}")

    async def send(self, message:str) -> str:
        async with self.discord_channel.typing():
            await asyncio.sleep(random.randint(3, 5))
            self.log.info(f"sending: {message}")
            res = await await_timeout(self.discord_channel.send(content=message))
            if res:
                self.set_last_message_sent_time_locked(time.time())
            else:
                self.log.warning("Could not finish sending message")
        return res

class AIGenType(enum.StrEnum):
    CHAT_RESPONSE = enum.auto()
    STATUS_ACTIVITY = enum.auto()
    TALKING_POINT = enum.auto()

class AIClient:
    def __init__(self, name:str):
        self.name = name

    async def ask_impl(self, type:AIGenType, sys_prompt:str,
                       prompt:str, model_temperature:float,
                       log:ChainedLogger) -> str | None:
        raise NotImplementedError()

    async def ask(self, type:AIGenType, sys_prompt:str,
                  prompt:str, model_temperature:float,
                  log:ChainedLogger) -> str | None:
        assert AIGenType(type)
        log = log.sub(self.name)
        prompt = re.sub(r'[\t\n\r]', '', prompt)
        log.info(f"AI ask: {type}")
        log.debug(f"\t{prompt}")
        res = await self.ask_impl(type, sys_prompt, prompt, model_temperature,
                                  log)
        log.info(f"Got {"Good" if res else "Bad"} AI response for {type}")
        log.debug(f"\t{res}")
        return res

class AIClientFake(AIClient):
    def __init__(self):
        super().__init__("FakeAI")

    async def ask_impl(self, type:AIGenType, sys_prompt:str,
                       prompt:str, model_temperature:float,
                       log:ChainedLogger) -> str | None:
        if type == AIGenType.CHAT_RESPONSE:
            return "fake chat response"
        elif type == AIGenType.STATUS_ACTIVITY:
            return "fake status activity"
        elif type == AIGenType.TALKING_POINT:
            return "fake talking point"
        else:
            return None

class AIClientMulti(AIClient):
    def __init__(self, clients:list[AIClient]):
        super().__init__("MultiAI")
        self.clients = clients
        self.client_failures = {}
        for c in clients:
            self.client_failures[c] = 0.0
        self.client_lock = asyncio.Lock()

    async def ask_impl(self, type:AIGenType, sys_prompt:str,
                       prompt:str, model_temperature:float,
                       log:ChainedLogger) -> str | None:
        async with self.client_lock:
            now = time.time()
            for force_check in [False, True]:
                for client in self.clients:
                    last_fail = self.client_failures[client]
                    if not force_check and (now - last_fail < \
                            min_to_s(OPTS.multi_client_backoff_min)):
                                continue
                    res = await client.ask(type, sys_prompt, prompt,
                                           model_temperature, log)
                    if res:
                        return res
                    self.client_failures[client] = now
                    log.warning("AIClient failure, falling back to next model")
        log.warning("All AIClient failed")
        return None

class AIClientGemini(AIClient):
    def __init__(self, client:gemini.Client, num:int):
        super().__init__(f"GeminiAI-{num}")
        self.client = client

    async def ask_impl(self, type:AIGenType, sys_prompt:str,
                       prompt:str, model_temperature:float,
                       log:ChainedLogger) -> str | None:
        config = gemini.types.GenerateContentConfig(
                system_instruction=sys_prompt,
                temperature=model_temperature,
                max_output_tokens=OPTS.ai_model_max_tokens)

        res = await await_timeout( \
                self.client.aio.models.generate_content( \
                    model=OPTS.gemini_model, contents=prompt, config=config), \
                fallback=None, logger=log)
        if not res:
            return
        reason = res.candidates[0].finish_reason
        if reason == gemini.types.FinishReason.STOP:
            return res.text
        log.warning(f"Gemini responded with status {reason.name}")
        return None

class AIClientClaude(AIClient):
    def __init__(self, client:AsyncAnthropic):
        super().__init__("ClaudeAI")
        self.client = client

    async def ask_impl(self, type:AIGenType, sys_prompt:str,
                       prompt:str, model_temperature:float,
                       log:ChainedLogger) -> str | None:
        res = await await_timeout(self.client.messages.create(
                model=OPTS.claude_model,
                max_tokens=OPTS.ai_model_max_tokens,
                # temperature=model_temperature, not used for Opus 4.7+
                system=sys_prompt,
                messages=[ {"role": "user", "content": prompt} ]),
            fallback=None, logger=log)

        if not res:
            return
        return res.content[0].text

class GnomeBot():
    @dataclasses.dataclass
    class Config():
        name: str
        discord_id: int
        prompt: str
        discord_token_env: str
        wakeup_hour: int
        bedtime_hour: int
        model_temperature: float

    def __init__(self, config:Config, ai_client:AIClient,
                 discord_intents:discord.Intents,
                 *args, **kwargs):
        self.log = root_logger.sub(config.name)
        self.ai_client = ai_client
        discord_token = os.getenv(config.discord_token_env)
        assert isinstance(discord_token, str)
        assert len(discord_token) > 0
        self.discord_token = discord_token
        self.discord_client = discord.Client(intents=discord_intents)
        setattr(self.discord_client, "on_error", self.on_error)
        setattr(self.discord_client, "on_ready", self.on_ready)
        self.config = config
        self.status = discord.Status.offline
        self.ready = False
        self.channels = {}

    def __get_unique_personality_prompt(self) -> str:
        assert self.discord_client.user
        p = f"You are {self.discord_client.user.display_name}, a Gnome in the World of Warcraft universe."
        p += self.config.prompt
        return p

    def get_chat_sys_prompt(self) -> str:
        p = self.__get_unique_personality_prompt()
        p += PROMPT_CORE
        p += PROMPT_RULES.format(OPTS.chat_max_sentences)
        return p

    def get_activity_sys_prompt(self) -> str:
        p = self.__get_unique_personality_prompt()
        p += PROMPT_CORE
        p += """Rules:
        - You must write a discord activity.
        - Your activity must be 50 characters or less.
        - Do not include quotes, markdown, or any other conversational text.
        """
        return p

    async def start_discord_client(self):
        assert self.discord_token is not None
        self.log.debug(f"Connecting to discord with {self.discord_token}")
        return await self.discord_client.start(token=self.discord_token)

    async def on_error(self, event_method, *args, **kwargs) -> None:
        self.log.critical(f"Error in event: {event_method}")
        traceback.print_exc(file=sys.stderr)

    def is_asleep(self) -> bool:
        return self.status == discord.Status.invisible

    async def update_status(self) -> None:
        activity = None

        now = datetime_now()
        weekday = now.weekday()
        if now.hour < self.config.wakeup_hour or \
                now.hour > self.config.bedtime_hour:
            status = discord.Status.invisible
        elif weekday >= 5:
            status = discord.Status.idle
        elif now.hour < 22:
            status = discord.Status.idle
        elif random.random() < 0.2:
            status = discord.Status.dnd
        else:
            status = discord.Status.online

        if status is None or status != self.status:
            if status == discord.Status.idle:
                text = await self.ai_client.ask(AIGenType.STATUS_ACTIVITY,
                        self.get_activity_sys_prompt(),
                        "Generate a status for a leisurely activity",
                        self.config.model_temperature, self.log)
            elif status == discord.Status.dnd:
                text = await self.ai_client.ask(AIGenType.STATUS_ACTIVITY,
                        self.get_activity_sys_prompt(),
                        "Generate a status for working on something important",
                        self.config.model_temperature, self.log)
            else:
                text = None
            activity = discord.CustomActivity(name=text) if text else None
            self.status = status
            await await_timeout(self.discord_client.change_presence(
                status=self.status, activity=activity), logger=self.log)
            self.log.info(f"Presence changed to {status}")

    async def tick(self) -> None:
        self.log.info("Tick..")
        await self.update_status()
        if self.is_asleep():
            self.log.debug("Asleep, nothing to do")
            return

        for channel in self.channels.values():
            assert channel.discord_user == self.discord_client.user
            async with channel.message_lock:
                now = time.time()
                dt = now - channel.get_last_message_sent_time_locked()
                self.log.debug(f"Last talked {dt}s ago")
                if dt <= min_to_s(30.0):
                    self.log.debug("Spoke too recently, do nothing")
                    continue

                self.log.info("Generating a new talking point")
                msg = await self.ai_client.ask(AIGenType.TALKING_POINT,
                                               self.get_chat_sys_prompt(),
                                               PROMPT_TALKING_POINT,
                                               self.config.model_temperature,
                                               self.log)
                if msg:
                    assert channel.discord_user == self.discord_client.user
                    await channel.send(msg)
                else:
                    self.log.warning("Wasn't able to generate a talking point")


    async def start_tick(self) -> None:
        while True:
            await asyncio.sleep(min_to_s(OPTS.tick_min))
            if not self.ready:
                continue
            await self.tick()

    class ShouldRespondType(enum.Enum):
        YES = 1
        NO = 2
        MUST = 3

    def should_respond(self, ctx:ContextualMessage,
                       channel:ChatChannel) -> ShouldRespondType:
        assert not ctx.from_self

        if self.is_asleep():
            return self.ShouldRespondType.NO

        respond_prob = 0.1

        # Base respond probs
        if not ctx.is_bot:
            if ctx.is_exclusive_mention or ctx.is_reply or ctx.is_mention:
                return self.ShouldRespondType.MUST

        # Mods
        dt_min = s_to_min(
                time.time() - channel.get_last_message_sent_time_locked())
        respond_prob *= min((dt_min * 0.01) + 0.5, 1.5)

        if ctx.keyword_match:
            respond_prob *= 2

        if ctx.user_info:
            mod = 0.8
            match ctx.user_info.type:
                case UserType.NEUTRAL:
                    mod = 1.0
                case UserType.GNOME:
                    mod = 1.5
                case UserType.GNOME_FRIEND:
                    mod = 1.3
                case UserType.GNOME_HATER:
                    mod = 1.8
                case UserType.TUCK:
                    mod = 2.0
                case _:
                    pass
            respond_prob *= mod

        if random.random() < respond_prob:
            return self.ShouldRespondType.YES
        return self.ShouldRespondType.NO

    async def rendezvous_message(self, msg_id:int, current:ShouldRespondType) \
            -> ShouldRespondType:
        # TODO: try to not all respond to the same messages
        return current

    async def on_message(self, msg:discord.Message) -> None:
        assert self.ready

        if msg.channel.id not in self.channels:
            return

        log = self.log.sub(f"msg-id:{msg.id}")
        log.info(f"Got message")
        assert self.discord_client.user is not None
        ctx = ContextualMessage(msg, self.discord_client.user)
        channel = self.channels[msg.channel.id]
        assert channel.discord_user == self.discord_client.user
        await channel.add_message_to_history(ctx)

        if ctx.from_self:
            return

        async with channel.message_lock:
            should_respond_type = self.should_respond(ctx, channel)
            if should_respond_type == self.ShouldRespondType.NO:
                log.info("Not responding")
                return

            should_respond_type = await self.rendezvous_message(msg.id,
                                                                should_respond_type)
            if should_respond_type == self.ShouldRespondType.NO:
                    log.info("Not responding after rendezvous")
                    return

            reply = await self.ai_client.ask(AIGenType.CHAT_RESPONSE,
                                             self.get_chat_sys_prompt(),
                                             await channel.get_chat_history(),
                                             self.config.model_temperature,
                                             self.log)
            if not reply:
                if should_respond_type == GnomeBot.ShouldRespondType.MUST:
                    reply = random.choice(UNAVAILABLE_FALLBACKS)
                else:
                    return
            await channel.send(reply)

    async def on_ready(self) -> None:
        assert self.discord_client.user
        assert self.discord_client.user.id == self.config.discord_id
        display_name = self.discord_client.user.display_name
        self.log.info(f"Discord Identity: {self.discord_client.user.name}"
                      f"({display_name})")
        async with users_lock:
            users[self.discord_client.user.id] = UserInfo(display_name,
                                                          UserType.GNOME)
        for id in WHITELIST_CHANNEL_IDS:
            discord_channel = self.discord_client.get_channel(id)
            assert isinstance(discord_channel, discord.TextChannel)
            channel = ChatChannel(discord_channel, self.discord_client.user,
                                  self.log)
            assert channel.discord_user == self.discord_client.user
            await channel.init_history()
            self.channels[id] = channel
        await self.update_status()
        self.ready = True
        setattr(self.discord_client, "on_message", self.on_message)
        self.log.info("ready!")
        if OPTS.tick_on_ready:
            await self.tick()


# Main
async def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if OPTS.debug_mode else logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    root_logger = ChainedLogger(logging.getLogger())

    if OPTS.debug_mode:
        root_logger.debug("DEBUG mode enabled")

    dotenv.load_dotenv()
    discord_intents = discord.Intents.default()
    discord_intents.message_content = True

    # Ordered by priority
    ai_clients = []
    if OPTS.enable_gemini2:
        ai_clients.append(AIClientGemini(
                gemini.Client(api_key=os.environ["GEMINI_API_KEY_2"]), 2))
    if OPTS.enable_gemini:
        ai_clients.append(AIClientGemini(
                gemini.Client(api_key=os.environ["GEMINI_API_KEY"]), 1))
    if OPTS.enable_claude:
        ai_clients.append(AIClientClaude(
                AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])))
    if OPTS.enable_fakeai:
        ai_clients.append(AIClientFake())
    ai_client = AIClientMulti(ai_clients)

    configs = [
    (GnomeBot.Config(
        # Quill Manabuckle
        name="Quill",
        discord_id=1508942598046224384,
        discord_token_env="QUILL_TOKEN",
        prompt=PROMPT_QUILL,
        model_temperature=OPTS.ai_model_temperature,
        bedtime_hour=23,
        wakeup_hour=7
    ), OPTS.enable_bot_quill),
    (GnomeBot.Config(
        # Wizzle Brassbolt
        name="Wizzle",
        discord_id=1511404158995927231,
        discord_token_env="WIZZLE_TOKEN",
        prompt=PROMPT_WIZZLE,
        model_temperature=OPTS.ai_model_temperature+0.1,
        bedtime_hour=23,
        wakeup_hour=7
    ), OPTS.enable_bot_wizzle),
    (GnomeBot.Config(
        # Bink Cogwhistle
        name="Bink",
        discord_id=1511406077374107690,
        discord_token_env="BINK_TOKEN",
        prompt=PROMPT_BINK,
        model_temperature=OPTS.ai_model_temperature-0.1,
        bedtime_hour=23,
        wakeup_hour=7
    ), OPTS.enable_bot_bink)]

    for config, enabled in configs:
        if enabled:
            root_logger.info(f"Booting up {config.name}")
            gnomes[config.discord_id] = GnomeBot(config, ai_client,
                                                 discord_intents)

    tasks = []
    tasks.extend([g.start_discord_client() for g in gnomes.values()])
    tasks.extend([g.start_tick() for g in gnomes.values()])
    root_logger.info("Let the chat begin...")
    await asyncio.gather(*tasks)
    root_logger.info("Chat done ? Probably no bots enabled")
    return 0

if __name__ == "__main__":
    asyncio.run(main())
