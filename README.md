# MCAD Telegram Bot

Checks BleepingComputer’s RSS feed every 15 minutes and sends links
to possible maritime cyber reports through Telegram.

The prototype uses keyword filtering, not AI. Reports need human review.

## Before starting

You need:

- A computer with an internet connection.
- Telegram and a Telegram account.
- The shared bot token from Mihaela, provided privately.

You do not need to create a new bot.

Each teammate uses:

- The same bot token.
- Their own personal Telegram chat ID.
- Their own local copy of the program.

Follow only the installation section for your operating system.
Run commands one at a time and wait for each to finish.

## 1A. Install tools on macOS

### Install Git

Open Terminal using Command + Space, type Terminal, and press Enter.

Run:

```bash
xcode-select --install
```

Follow the installation window and wait until it finishes.
If the tools are already installed, continue.

Check Git:

```bash
git --version
```

### Install Python

Open the official download page:

```bash
open https://www.python.org/downloads/macos/
```

Download a standard Python 3.13 macOS installer and complete
the installation.

Then run:

```bash
open "/Applications/Python 3.13/Install Certificates.command"
```

Wait for the certificate installation to finish.

Close and reopen Terminal, then check:

```bash
python3.13 --version
```

### Install VS Code

Open:

```bash
open https://code.visualstudio.com/
```

Download the Mac version, open the downloaded file, and move
Visual Studio Code to Applications.

### Install Telegram

Open:

```bash
open https://telegram.org/apps
```

Install Telegram, or use the Telegram app already on your phone.
Sign in to your account.

Continue to section 2.

## 1B. Install tools on Windows

Open PowerShell from the Start menu.

Check whether Windows Package Manager is available:

```powershell
winget --version
```

If winget is not recognised, install or update “App Installer”
from the Microsoft Store, then reopen PowerShell.

Install Git:

```powershell
winget install --id Git.Git --exact --source winget
```

Install Python:

```powershell
winget install --id Python.Python.3.13 --exact --source winget
```

Install VS Code:

```powershell
winget install --id Microsoft.VisualStudioCode --exact --source winget
```

Accept installation prompts when asked.

Close and reopen PowerShell so it recognises the installed tools.

Check:

```powershell
git --version
py -3.13 --version
```

For Telegram, open its official download page:

```powershell
Start-Process "https://telegram.org/apps"
```

Install Telegram, or use the app on your phone.
Sign in to your account.

Continue to section 2.

## 1C. Install tools on Ubuntu Linux

These commands are for Ubuntu. Other Linux distributions may
require different installation commands.

Open Terminal and run:

```bash
sudo apt update
```

```bash
sudo apt install -y git python3 python3-venv python3-pip snapd
```

Install VS Code:

```bash
sudo snap install code --classic
```

Check:

```bash
git --version
python3 --version
```

Use a currently supported Ubuntu release with Python 3.10 or newer.

Install Telegram from https://telegram.org/apps, or use the app
on your phone. Sign in to your account.

Continue to section 2.

## 2. Clone the project

In Terminal or PowerShell, run:

```bash
git clone https://github.com/MichelleCovali/mcad-bot.git
```

```bash
cd mcad-bot
```

This creates the project inside your current folder.

If you already cloned it, enter your existing mcad-bot folder
and update it instead:

```bash
git pull
```

If the repository is private, ask Mihaela for access and follow
GitHub's authentication prompts.

## 3. Open the project in VS Code

1. Open Visual Studio Code.
2. Choose File → Open Folder.
3. Select the mcad-bot folder.
4. Open Extensions in the left sidebar.
5. Install the Python extension published by Microsoft.
6. Choose Terminal → New Terminal.

The terminal should be inside mcad-bot.

Check the project files:

```bash
git ls-files
```

You should see files including:

- bot.py
- news.py
- filters.py
- setup_telegram.py
- requirements.txt
- .env.example
- .gitignore

If setup_telegram.py or .env.example is missing, ask Mihaela
to push those files before continuing.

## 4. Create the Python environment

### macOS

```bash
python3.13 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
python -m pip install --upgrade pip
```

```bash
python -m pip install -r requirements.txt
```

### Ubuntu Linux

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
python -m pip install --upgrade pip
```

```bash
python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
py -3.13 -m venv .venv
```

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
```

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

On Windows, these commands use the environment directly.
You do not need to activate it or change PowerShell's execution policy.

### Select the Python interpreter

In VS Code:

1. Open the Command Palette:
   - Mac: Command + Shift + P
   - Windows or Linux: Control + Shift + P
2. Search for Python: Select Interpreter.
3. Select the interpreter inside .venv.

## 5. Create your private settings file

Do this only if you do not already have a .env file.

On macOS or Linux:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

In VS Code, open .env and enter:

```text
TELEGRAM_BOT_TOKEN=paste_the_token_from_Mihaela_here
TELEGRAM_CHAT_ID=
```

Replace the token placeholder with the real token.
Leave TELEGRAM_CHAT_ID empty for now.

Save the file.

Never upload .env to GitHub. Never share screenshots containing
the token.

Check that Git ignores it:

```bash
git check-ignore .env
```

Expected output:

```text
.env
```

If nothing appears, stop and check that .gitignore includes:

```text
.venv/
.env
__pycache__/
sent_links.json
```

Do not put a real token in .env.example.

## 6. Open the shared bot in Telegram

Open:

https://t.me/MCADAlertsBot

Press Start.

The bot will not reply automatically. That is expected.

## 7. Save your personal chat ID

Coordinate with the team before this step.

Only one person should run setup_telegram.py at a time.
Other tools reading this bot's incoming messages should be stopped.

On macOS or Linux:

```bash
python setup_telegram.py
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe setup_telegram.py
```

The script shows a unique code, for example:

```text
setup-a1b2c3d4
```

1. Send your exact code to @MCADAlertsBot in Telegram.
2. Return to the terminal.
3. Press Enter.

Expected result:

```text
Success! Your chat ID was saved in .env.
```

Your private .env now contains:

- The shared token.
- Your personal chat ID.

You do not need to enter the chat ID manually.

## 8. Send a test message

On macOS or Linux:

```bash
python -c "from bot import send_message; print('Sent:', send_message('MCAD connection test'))"
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -c "from bot import send_message; print('Sent:', send_message('MCAD connection test'))"
```

Expected output:

```text
Sent: True
```

Check Telegram. You should receive:

```text
MCAD connection test
```

This confirms your token and chat ID work.

## 9. Test the news feed

On macOS or Linux:

```bash
python news.py
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe news.py
```

You should see the number of articles found and up to five
article titles and links.

## 10. Test the maritime filter

On macOS or Linux:

```bash
python filters.py
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe filters.py
```

The filter displays matching articles, if any.

Zero real matches is normal when the recent feed contains
no matching maritime reports.

The fictional example in this script is only a local filter test.
It does not send anything to Telegram.

## 11. Start automatic checks

On macOS or Linux:

```bash
python bot.py
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe bot.py
```

Expected output looks like:

```text
MCAD bot started. Checking every 15 minutes.
Press Control + C to stop.
Checked 15 articles.
Sent 0 new links.
Waiting 15 minutes before the next check...
```

The article count may differ.

The bot sends only matching links that this local copy has not
already sent. It does not send a message after every check.

Keep your computer awake, connected to the internet, and the
program running.

Closing the terminal or shutting down the computer stops the bot.

To stop it, press Control + C.

On macOS, you can optionally run it with:

```bash
caffeinate -i python bot.py
```

This prevents idle system sleep while the command runs.
Keep the laptop lid open.

## 12. Start it again another day

Open the mcad-bot folder in VS Code and open a terminal.

On macOS or Linux:

```bash
source .venv/bin/activate
```

```bash
python bot.py
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe bot.py
```

You do not need to repeat the token or chat ID setup.

## 13. Get project updates

Stop the running bot with Control + C.

From the project folder, run:

```bash
git pull
```

Update dependencies.

On macOS or Linux:

```bash
source .venv/bin/activate
```

```bash
python -m pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then start bot.py again.

If git pull reports conflicting local changes, ask for help
before deleting or overwriting anything.

## Troubleshooting

### A command is not recognised

After installing tools, close and reopen your terminal.
Check that you followed the instructions for your operating system.

### No module named requests, dotenv, or feedparser

Repeat the dependency installation commands in section 4.
Make sure VS Code is using the .venv interpreter.

### The setup script cannot find my message

Run setup_telegram.py again.

Send the new code to the correct bot before pressing Enter.
Ensure nobody else is running the setup script simultaneously.

### The Telegram test fails

Check that:

- You pressed Start in the bot's Telegram chat.
- .env is beside bot.py.
- The token is current and copied correctly.
- setup_telegram.py saved your chat ID.
- You saved .env after editing it.

Do not paste your token into an issue or screenshot.

### No incident links arrive

Run the connection test in section 8.

If it works, Telegram is configured correctly.
The current news feed may simply contain no matching reports.

### Could not retrieve the news feed

Check your internet connection and try again later.
The source may be temporarily unavailable or restricting requests.

### Sending history cannot be read

Stop the bot and ask for help checking sent_links.json.
Deleting it can cause previously sent links to be sent again.

## Project files

| File              | Purpose                                       |
| ----------------- | --------------------------------------------- |
| bot.py            | Telegram sending, history, and regular checks |
| news.py           | RSS feed reader                               |
| filters.py        | Maritime keyword filtering                    |
| setup_telegram.py | Personal chat ID setup                        |
| requirements.txt  | Python dependencies                           |
| .env.example      | Configuration placeholders                    |
| .env              | Private configuration, excluded from Git      |
| sent_links.json   | Local sent-link history, excluded from Git    |

The history file is created after the first article link is sent.
The connection test does not create it.

## Important limitations

- This is a keyword-based prototype, not an AI classifier.
- It checks titles and feed summaries, not full article text.
- It can miss incidents or flag irrelevant reports.
- It checks one source's recent feed, not its full archive.
- Different links about the same incident may be sent separately.
- Sending history is local to each computer.
- Do not run two copies using the same destination chat.
- A crash between sending and saving history can cause a repeat.
- macOS has been tested; Windows and Linux still need team testing.

Every candidate requires human review before inclusion in MCAD.

## Shared token

All teammates use the same token but different personal chat IDs.

Anyone with the token can control the shared bot and potentially
access messages sent to it. Share it only with trusted teammates.

If the token is exposed, Mihaela should revoke it through BotFather.
Everyone must then update their local .env with the replacement.
