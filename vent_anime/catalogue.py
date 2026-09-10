"""What the anime module knows the names of, in one place.

Genres, the kinds of comic, the states a series can be in, the attributes a
character is voted on, and the annotation shapes. Every one of them is a table
here rather than a set of choices repeated across a model, a serializer, a
filter and a screen: the format catalogue drifted into five copies once already
on this platform, and `tools/check-format-catalogue.py` exists because of it.

The frontend reads these from `/anime/catalogue/` rather than holding its own
copy, so a genre added here is on the filter the same day.
"""

#: What somebody uploads. The spec names three, and they are genuinely
#: different reading experiences rather than a label: a manga is read right to
#: left, a manhwa is a single vertical strip, a webcomic is page by page. The
#: reader defaults its mode from this.
KINDS = {
    'manga': 'Manga',
    'manhwa': 'Manhwa',
    'webcomic': 'Webcomic',
}

#: The default reading mode each kind wants. A manhwa opened two pages side by
#: side is unreadable, and asking every author to set it is asking them to know
#: what the reader's screen looks like.
DEFAULT_MODE = {
    'manga': 'single',
    'manhwa': 'vertical',
    'webcomic': 'single',
}

STATUSES = {
    'ongoing': 'Ongoing',
    'completed': 'Completed',
    'hiatus': 'On hiatus',
}

#: Genres. Taken from what the designed screen already offered rather than
#: invented, so the filter that exists keeps working.
GENRES = {
    'shonen': 'Shonen',
    'seinen': 'Seinen',
    'shojo': 'Shojo',
    'isekai': 'Isekai',
    'mecha': 'Mecha',
    'fantasy': 'Fantasy',
    'cyberpunk': 'Cyberpunk',
    'action': 'Action',
    'drama': 'Drama',
    'adventure': 'Adventure',
    'sports': 'Sports',
    'romance': 'Romance',
    'horror': 'Horror',
    'comedy': 'Comedy',
    'slice_of_life': 'Slice of life',
}

#: How a series is paid for. One of three, chosen by the author, and it decides
#: which question `access.may_read` asks.
PRICING = {
    'free': 'Free for everybody',
    'per_chapter': 'Paid, chapter by chapter',
    'subscription': 'A subscription to the series',
}

VISIBILITY = {
    'public': 'Anybody can find it',
    'private': 'Only me',
}

#: Room privacy. The spec asks for exactly these three.
ROOM_PRIVACY = {
    'public': 'Anybody can join',
    'private': 'Only people I invite',
    'password': 'Anybody with the password',
}

#: Who may turn the page in a room.
ROOM_CONTROL = {
    'host': 'The host turns the page',
    'anyone': 'Anybody can turn the page',
}

#: What a participant can leave on a page.
ANNOTATION_KINDS = {
    'highlight': 'Highlighted area',
    'note': 'Sticky note',
    'drawing': 'Drawing',
}

#: The attributes a character is voted on in a battle.
#:
#: Five, and the list is fixed rather than per-battle, because a battle whose
#: attributes differ from the next one cannot be compared with it and the whole
#: point of the algorithm is a comparison. `strength` and `speed` come straight
#: from the spec; the other three are the smallest set that makes a fight
#: describable without turning into a character sheet.
ATTRIBUTES = {
    'strength': 'Strength',
    'speed': 'Speed',
    'intelligence': 'Intelligence',
    'durability': 'Durability',
    'technique': 'Technique',
}

#: The scale a vote is on. 1 to 10, inclusive.
VOTE_MIN = 1
VOTE_MAX = 10

BATTLE_STATES = {
    'nominating': 'Taking nominations',
    'voting': 'Voting is open',
    'closed': 'Decided',
}

#: Reading modes. `vertical` is the endless strip a manhwa wants, `double`
#: is two pages side by side on a wide screen, `single` is one page.
READING_MODES = {
    'single': 'One page',
    'double': 'Two pages',
    'vertical': 'Keep scrolling',
}

#: Reader themes. PREMIUM, per the spec, which marks "Themes and Layouts" and
#: nothing else in that block. The modes and the font size are free, because
#: they are how somebody reads at all rather than a decoration.
THEMES = {
    'dark': 'Dark',
    'light': 'Light',
    'sepia': 'Sepia',
    'black': 'True black',
}

#: The free theme. Everything else needs premium, and the screen says so before
#: anybody presses.
FREE_THEME = 'dark'


def label(table, key, fallback=''):
    """A name for a key, or the fallback. Never a KeyError on a screen."""
    return table.get(key, fallback or key)
