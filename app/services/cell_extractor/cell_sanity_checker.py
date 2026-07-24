import re


def check_cell_title(title):
    """
    Example check: ensure notebook title does not start with a digit.
    """
    # Title should only start letters
    # Remove first '#' from title
    if title[0] == '#':
        title = title[1:]
    title = title.strip()
    # Title should start only with letters
    if re.match(r'^[^a-zA-Z]', title):
        raise ValueError('Title: ' + title + ' is not a valid. Title should '
                                             'start with a letter.')
    # Tile should not contain special characters
    if re.search(r'[^\w\s.:-]', title):
        raise ValueError(
            'Title: ' + title + ' is not a valid. Title should not'
                                ' contain special characters except'
                                ' for - , . , _ and :')
