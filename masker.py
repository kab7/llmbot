from parser import Parser


def mask(text, replacer=''):
    if not text:
        return ''

    parser = Parser()
    tokens = parser.parse(text)

    if not tokens:
        return text

    masked_parts = []
    cursor = 0

    for token in tokens:
        masked_parts.append(text[cursor : token.start])
        masked_parts.append(replacer)
        cursor = token.end

    if cursor < len(text):
        masked_parts.append(text[cursor:])

    return ''.join(masked_parts)
