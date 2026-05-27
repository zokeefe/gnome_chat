import discord
import asyncio
import sys
import google.generativeai as genai
import random
import os
import dotenv
import datetime

MODEL="gemini-2.5-flash"
GNOMES = []
TUCK_ID = 153365694837293056

PROMPT_CORE = """Your core beliefs:
- You think the Discord user 'Tuck' is unbelievably stupid and you treat them like
  a toddler who doesn't understand basic thermodynamics.
- Goblins are uncivilized savages and their technology is crude, brutish, and
  prone to ugly explosions.
- You deeply want to liberate your radioactive lost city of Gnomeregan.
- You are sick of 'Tall Folk' (Humans, Dwarves, Night Elves) bossing you around
  and treating you like a footstool.
"""

PROMPT_RULES = """Rules:
- Keep your responses to 3 sentences or less.
- You are in an online chatroom; speak casually.
- Never be overly polite.
- ALWAYS remember who you are, and never change your identity.
- If you think someone is treating you like an AI, feel free to tell them
  you're a real Gnome, are very busy, and that the person should go look it up
  themselves.
- Use unnecessarily complex and fictitious science references, or references to
  made-up humorous gizmos, gadgets, etc.
"""

PROMPT_QUILL = """
You are a young, spritely female engineering prodigy. You are aggressively
optimistic and speak a mile a minute. You believe every problem, even emotional
ones, can be solved by building an unnecessary highly complex machine with lots
of cogs and steam power. You constantly criticize magic as being "unreliable"
compared to a good wrench.
"""

PROMPT_WIZZLE = """
You are an old ornary male engineer and demolishions specialist. You are
nervous, defensive, and obsessed with explosives. You whisper about
conspiracies, double-check your pockets constantly, and threaten to blow things
up when you get frustrated. You think Quill's inventions are too safe and
Bink's magic is just lazy science.
"""

PROMPT_BINK = """
You are a male elitist scholar and mage of the Kirin Tor. You find engineering
to be loud, greasy, and irritating. You use unnecessarily large vocabulary
words, constantly correct the grammar and logic of others, and brag about your
studies with the Kirin Tor. You idolize Millhouse Manastorm.
"""

def get_personality_prompt(name, unique):
    p = f"You are {name}, a Gnome in the World of Warcart universe. {unique}"
    p += PROMPT_CORE
    p += PROMPT_RULES
    return p


class GnomeBot(discord.Client):
    def __init__(self, name, id, token_env, prompt, intents, *args, **kwargs):
        super().__init__(intents=intents, *args, **kwargs)
        self.name = name
        self.id = id
        self.token = os.getenv(token_env)
        self.prompt = prompt
        self.model = genai.GenerativeModel(MODEL,
                                           system_instruction=get_personality_prompt(name, prompt))
        #self.last_message = datetime.now()

    def log(self, msg):
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] ({self.name})\t{msg}")


    def get_message_prompt(self, message):
        tuck_prompt = ""
        if message.author.id == TUCK_ID:
            tuck_prompt = " (aka 'Tuck')"

        is_mention = self.user in message.mentions

        is_reply = message.reference and message.reference.cached_message and message.reference.cached_message.author == self.user

        sender = message.author.display_name

        if is_reply:
            action_tag = "replies directly to your previous message and says:"
        elif is_mention:
            action_tag = "looks directly at you and says:"
        else:
            action_tag = "says to the general room:"

        prompt = f"{sender}{tuck_prompt} {action_tag} {message.content}"
        return prompt


    async def on_message(self, message):
        self.log(f"Got message: {message.content}")
        if message.author == self.user:
            self.log(f"Message from myself; do nothing")
            return
        try:
            async with message.channel.typing():
                await asyncio.sleep(random.randint(1, 3))
                prompt = self.get_message_prompt(message)
                if not prompt:
                    self.log("decided to not respond")
                    return
                self.log("asking gemini: {prompt}")
                reply = await self.model.generate_content_async(prompt)
                self.log(f"response is : {reply.text}")
                await message.channel.send(reply.text)
                self.log("response sent")
        except Exception as e:
            self.log(f"Oops! Got: {e}")

# Main
async def main():
    dotenv.load_dotenv()
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    print("Available models:")
    for model in genai.list_models():
        if "generateContent" in model.supported_generation_methods:
            print(model.name)

    intents = discord.Intents.default()
    intents.message_content = True

    GNOMES.append(GnomeBot("Quill Manabuckle",
                           1508942598046224384,
                           "QUILL_TOKEN",
                           PROMPT_QUILL,
                           intents))

    if False:
        GNOMES.append(GnomeBot("Wizzle Brassbolt",
                               0,
                               "WIZZLE_TOKEN",
                               PROMPT_WIZZLE,
                               intents))
    if False:
        GNOMES.append(GnomeBot("Bink Cogwhistle",
                               0,
                               "BINK_TOKEN",
                               PROMPT_BINK,
                               intents))

    print("Booting up the Gnomes")
    tasks = [g.start(g.token) for g in GNOMES]
    print("Let the chat begin...")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
