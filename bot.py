#!/usr/bin/env python
'''
    redeem_code
    ===========

    Discord bot to track and fetch redeem codes from the Love Nikki Wiki.

    Dependencies
    ============
        requests>=2.20
        discord.py>=2.1
        beautifulsoup4>=4.11
        python-dotenv>=0.20

    Config
    ======

    This requires a `.env` file containing the `DISCORD_TOKEN` and
    the `DISCORD_CHANNEL` or the values to be provided on the command line.
    A sample file would be:
        DISCORD_TOKEN=xODk3NTk4NpeB7jMwMTYzMTU.43QYjx.4k4h_H-i8feb_rY5OTk07bYv9xI
        DISCORD_CHANNEL=189716987098470

    You can also add the following environment variables as well:
        - `DEFAULT_APPLICATION_ID`
        - `DEFAULT_MEMO_FILENAME`
        - `DEFAULT_INVERVAL`
        - `DEFAULT_LOG_FILE`
        - `DEFAULT_LOG_LEVEL`
        - `DEFAULT_PUBLIC_KEY`
        - `DEFAULT_WIKI_URL`
'''

import argparse
import discord
import discord.ext.tasks
import dotenv
import logging
import os
import requests
import requests.adapters
import urllib3.util.retry
from bs4 import BeautifulSoup

__version__ = '0.0.0-dev'

dotenv.load_dotenv()


def get_environment_value(key, default=None):
    value = os.getenv(key)
    return value or default


DEFAULT_WIKI_URL = get_environment_value(
    key='DEFAULT_WIKI_URL',
    default='https://lovenikki.fandom.com/wiki/Category:Redeem_Code',
)
DEFAULT_APPLICATION_ID = get_environment_value(
    key='DEFAULT_APPLICATION_ID',
    default='1048611969034375198',
)
DEFAULT_PUBLIC_KEY = get_environment_value(
    key='DEFAULT_PUBLIC_KEY',
    default='5fdeee3dcbbf27e083bc1a9627b47d95a1dd8fe86c86270102c72a0ace66c5fc',
)
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
DISCORD_CHANNEL = os.getenv('DISCORD_CHANNEL')
DEFAULT_INVERVAL = get_environment_value(
    key='DEFAULT_INVERVAL',
    default='240',
)
DEFAULT_MEMO_FILENAME = get_environment_value(
    key='DEFAULT_MEMO_FILENAME',
    default='redeem-codes.txt',
)
DEFAULT_LOG_LEVEL = os.getenv('DEFAULT_LOG_LEVEL')
DEFAULT_LOG_FILE = os.getenv('DEFAULT_LOG_FILE')
INTENTS = discord.Intents()
CLIENT = discord.Client(intents=INTENTS)
CODE_MEMO = set()

ARGPARSER = argparse.ArgumentParser(
    description='Discord bot for Love Nikki Redeem Codes.',
)
ARGPARSER.add_argument(
    '--application-id',
    help='Application ID of the Discord bot.',
    default=DEFAULT_APPLICATION_ID,
)
ARGPARSER.add_argument(
    '--public-key',
    help='Public key for the Discord bot user.',
    default=DEFAULT_PUBLIC_KEY,
)
ARGPARSER.add_argument(
    '--wiki-url',
    help='URL to fetch the codes from the Love Nikki Wiki.',
    default=DEFAULT_WIKI_URL,
)
ARGPARSER.add_argument(
    '--discord-token',
    help='Token for the Discord bot.',
    default=DISCORD_TOKEN,
)
ARGPARSER.add_argument(
    '--discord-channel',
    help='Unique ID for the Discord channel.',
    default=DISCORD_CHANNEL,
)
ARGPARSER.add_argument(
    '--interval',
    help='Interval (in minutes) to check for new redeem codes.',
    type=int,
    default=int(DEFAULT_INVERVAL),
)
ARGPARSER.add_argument(
    '--memo-filename',
    help='Filename to store the current memo at.',
    default=DEFAULT_MEMO_FILENAME,
)
ARGPARSER.add_argument(
    '--log-level',
    help='Threshold level for the logger.',
    default=DEFAULT_LOG_LEVEL,
)
ARGPARSER.add_argument(
    '--log-file',
    help='Path to file for the logger (None for stdout).',
    default=DEFAULT_LOG_FILE,
)
ARGUMENTS = ARGPARSER.parse_args()
LOGGER = logging.getLogger('dicord')
if ARGUMENTS.log_file is not None:
    HANDLER = logging.FileHandler(
        filename=ARGUMENTS.log_file,
        encoding='utf-8',
    )
else:
    HANDLER = logging.StreamHandler()
LOGGER.addHandler(HANDLER)
if ARGUMENTS.log_level is not None:
    LOGGER.setLevel(getattr(logging, ARGUMENTS.log_level.upper()))


def retry_session(retries, session=None, backoff_factor=0.3):
    '''Ensure a single request failure doesn't fail the bot.'''

    session = session or requests.Session()
    retry = urllib3.util.retry.Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        allowed_methods=('get'),
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


def get_redeem_code_table(url):
    '''Get the HTML table containing the redeem codes.'''

    session = retry_session(retries=5)
    response = session.get(url)
    response.raise_for_status()
    content = response.content
    soup = BeautifulSoup(content, 'html.parser')
    elements = soup.select('table.redeemcode')
    if len(elements) != 1:
        msg = f'Got unexpected number of items, raw HTML output is "{content}"'
        LOGGER.error(msg)
        raise ValueError(msg)
    return elements[0]


def get_table_row_text(row):
    '''Extract the table row data if the header matches the condition'''

    header = row.find(name='th')
    if header is None:
        return
    header_text = header.text.strip()

    data = row.find(name='td')
    if data is None:
        return
    data_text = data.text.strip()

    return (header_text, data_text)


def chunks_exact(iterable, interval):
    chunk = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) == interval:
            yield chunk
            chunk = []

    if chunk:
        msg = f'Did not exact interval, had {len(chunk)} leftover items'
        LOGGER.error(msg)
        raise ValueError(msg)


def parse_chunked_rows(chunk):
    data = {}
    for row in chunk:
        row_data = get_table_row_text(row)
        if row_data is not None:
            data[row_data[0]] = row_data[1]

    return data


def get_redeem_codes(table, memo):
    '''Process all the rows in the table to extract the redeem codes'''

    # redeem codes have the following format in the table
    # <table>
    #   <tbody>
    #   <!-- Single occurence -->
    #   <tr><th ...>Currently Active Redeem Codes</th></tr>
    #   <!-- Repeating occurences for all 3 elements -->
    #   <tr><th>Code</th><td>aFCaP4fSqejW</td></tr>
    #   <tr><th>Rewards</th><td>...</td></tr>
    #   <tr><th>Dates</th><td>November 28 – December 31, 2022</td></tr>
    #   </tbody>
    # </table>

    added = []
    current = set()

    rows = table.find_all(name='tr')
    if rows is None:
        return added
    elif (len(rows) - 1) % 3 != 0:
        msg = f'Expected 1 + 3*N rows, instead got table output of "{table}"'
        LOGGER.error(msg)
        raise ValueError(msg)

    for chunk in chunks_exact(rows[1:], 3):
        code_data = parse_chunked_rows(chunk)
        if 'Code' not in code_data:
            message = f'Got invalid for data for table "{table}"'
            LOGGER.error(message)
            raise ValueError(message)
        code = code_data['Code']
        current.add(code)
        if code not in memo:
            added.append(code)

    memo.clear()
    memo.update(current)
    with open(ARGUMENTS.memo_filename, 'w') as file:
        file.write('\n'.join(sorted(current)))

    return added


@CLIENT.event
async def on_ready():
    LOGGER.info('Starting up Discord client at `on_ready`.')

    if not fetch_and_send_codes.is_running():
        fetch_and_send_codes.start()
        LOGGER.info('Started fetch and send codes bot.')


@discord.ext.tasks.loop(minutes=ARGUMENTS.interval)
async def fetch_and_send_codes():
    LOGGER.info('Attempting to fetch redeem codes.')

    channel = await CLIENT.fetch_channel(ARGUMENTS.discord_channel)
    table = get_redeem_code_table(ARGUMENTS.wiki_url)
    added = get_redeem_codes(table, CODE_MEMO)
    memo = ', '.join(CODE_MEMO)
    LOGGER.info(f'Fetched codes and have current memo of [{memo}]')
    if added:
        delimiter = '\n  • '
        formatted = delimiter + delimiter.join(added)
        message = f'@redeemcodes Newly added redeem codes are: {formatted}'
        LOGGER.info(f'Sending channel message of {repr(message)}.')
        await channel.send(message)


@fetch_and_send_codes.before_loop
async def wait_login():
    LOGGER.info('Waiting for client login')
    await CLIENT.wait_until_ready()
    LOGGER.info('Client logged in')


def main():
    if ARGUMENTS.discord_token is None:
        message = 'Did not provide Discord bot token.'
        LOGGER.fatal(message)
        raise ValueError(message)
    if ARGUMENTS.discord_channel is None:
        message = 'Did not provide Discord channel ID.'
        LOGGER.fatal(message)
        raise ValueError(message)

    if os.path.exists(ARGUMENTS.memo_filename):
        with open(ARGUMENTS.memo_filename) as file:
            redeem_codes = file.read().splitlines()
            # can get extra newlines or empty entries
            CODE_MEMO.update([i for i in redeem_codes if i])

    LOGGER.info(f'Started Discord bot with codes of [{", ".join(CODE_MEMO)}]')
    CLIENT.run(ARGUMENTS.discord_token, log_handler=HANDLER)


if __name__ == '__main__':
    main()
