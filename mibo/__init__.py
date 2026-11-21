"""mibo — the application package.

The app's page (welcome.py) holds the character and reaches it through
character.py. The character is ``Agent``, configured by one settings record and
composed from one mixin per concern:

- settings   everything the agent is configured with, built once and passed in
- agent      wiring: applies the settings and builds the runtime state
- turns      the turn being answered: what was chosen for it, its time
- dm         the chat with the one person: a message read, a turn of the model
- messages   reading what the person sent: words, links, pictures, files
- decision   what becomes of a draft
- followup   asking after a plan, the one thing said unasked
- llm        model calls: endpoints, retries, fallback, probes
- prompts    the persona contract (style guide, output protocol)
- textproc   pure text: tokenising, sanitising, the prompt's data frames
- ingestion  links, pictures, vision, with the SSRF guard
- memory     the one person the app is for (person.py), and the chats' turns

Supporting modules, pure logic with no agent state:

- endpoints  which endpoint serves a model name
- channels   the keys a chat goes by
- config_env the one way to read a setting out of the environment

The entry point is the `mibo` command (cli.py), which opens the app.
Read-only seed datasets ship with the package (paths.seed_file); everything
the agent keeps at runtime goes to <home>/runtime/ (paths.ROOT is the home).
"""

from mibo import consoles

__version__ = "0.1.0"

# Started without a console (pythonw, a packaged shell): what it starts opens no console window.
consoles.hush()
