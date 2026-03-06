import re
import phonenumbers

from pii_token import Token, TokenType


class Parser:
    def __init__(self):
        self._specifications = self._get_specifications()

    def _get_specifications(self):
        # Паттерны для телефонов разных стран
        phone_11_13_pattern = r'((?:^)(?:(?:\+|%2B)(?:86|49))(?:[ \-\(\)]*\d){11,13})'
        phone_10_pattern = r'((?:^)(?:(?:\+|%2B)(?:977|856|234|225|92|91|90|58|57|44|39|7)|8)(?:[ \-\(\)]*\d){10})'
        phone_9_pattern = r'((?:^)(?:(?:\+|%2B)(?:998|996|995|994|992|972|971|970|966|381|380|375|358|264|263|260|258|256|254|251|249|244|243|242|237|233|221|212|211|51|48|34|33|27))(?:[ \-\(\)]*\d){9})'  # noqa: E501
        phone_8_pattern = r'((?:^)(?:(?:\+|%2B)(?:993|974|972|968|965|855|591|507|504|503|502|374|373|372|371|370|267|235|230|229|228))(?:[ \-\(\)]*\d){8})'
        phone_7_pattern = r'((?:^)(?:(?:\+|%2B)(?:372|267))(?:[ \-\(\)]*\d){7})'
        phone_10_pattern_russia_exception = r'((?:^)(?:(?:\+|%2B)?(?:7|8[\s]+)?)(?:[\(]*\d){1}(?:[ \-\(\)]*\d){9})'
        phone_10_pattern_russia_exception_2 = r'((?:^)(?:[ \-\(\)]*8)(?:[ \-\(\)]*\d){10})'

        # Паттерн для email
        email_pattern = r"""((?:^)(?:(?:[^\(\)\[\]\\;:,<>@.!#\$%&'""`*+/=?^_{|}~\-\s](?:[a-zA-ZЁёА-я0-9!#\$%&'`*+/=?^_{|}~\-]|(?:\.(?:[^.])))*[^\(\)\[\]\\;:,<>@.!#\$%&'""`*+/=?^_{|}~\-]|(?:%[0-9A-Fa-f]{2})+)(?:@|%40)(?:[a-zA-ZЁёА-я0-9.-]+\.[a-zA-ZЁёА-я]{2,})))"""  # noqa: E501

        # Паттерн для остальных символов (необходим для корректной работы парсера)
        rest_pattern = r'((?:^(?:[\S])|(?:[\s])))'

        return [
            (re.compile(email_pattern), TokenType.EMAIL),
            (re.compile(phone_11_13_pattern), TokenType.PHONE),
            (re.compile(phone_10_pattern), TokenType.PHONE),
            (re.compile(phone_9_pattern), TokenType.PHONE),
            (re.compile(phone_8_pattern), TokenType.PHONE),
            (re.compile(phone_7_pattern), TokenType.PHONE),
            (re.compile(phone_10_pattern_russia_exception_2), TokenType.PHONE_RU_EXCEPTION),
            (re.compile(phone_10_pattern_russia_exception), TokenType.PHONE_RU_EXCEPTION),
            (re.compile(rest_pattern), TokenType.REST),
        ]

    def parse(self, text: str):
        tokens = []
        cursor = 0
        text_len = len(text)

        while cursor < text_len:
            sub_text = text[cursor:]
            previous_cursor = cursor

            for pattern, token_type in self._specifications:
                match = pattern.match(sub_text)
                if not match:
                    continue

                groups = match.groups()

                match_text = groups[-1]
                match_len = len(match_text)

                token_start = cursor
                token_end = token_start + match_len

                if token_type is not TokenType.REST:
                    match_text = match_text.strip()
                    match_len = len(match_text)

                    substr = text[cursor:]
                    match_start = substr.find(match_text)

                    token_start = cursor + match_start
                    token_end = token_start + match_len

                is_pd = self._parse_personal_data(match_text, token_type)

                if is_pd:
                    tokens.append(
                        Token(
                            type=token_type,
                            start=token_start,
                            end=token_end,
                        )
                    )

                cursor = token_end
                break

            if cursor == previous_cursor:
                break

        return tokens

    def _parse_personal_data(self, match_text, token_type):
        if token_type == TokenType.REST:
            return False

        if token_type == TokenType.EMAIL:
            return True

        if token_type == TokenType.PHONE:
            return self._parse_phone(match_text, None)

        if token_type == TokenType.PHONE_RU_EXCEPTION:
            phone_prediction = self._predict_russian_phone(match_text)
            if phone_prediction is None:
                return False

            return self._parse_phone(phone_prediction, None)

        return False

    def _predict_russian_phone(self, match_text):
        maybe_phone = ''.join([char for char in match_text if char.isdigit()])
        if len(maybe_phone) == 11 and maybe_phone[0] == '7':
            return '+' + maybe_phone

        if len(maybe_phone) == 10:
            return '+7' + maybe_phone

        if len(maybe_phone) == 11 and maybe_phone[0] == '8':
            return '+7' + maybe_phone[1:]

        return None

    def _parse_phone(self, match_text, locale=None):
        try:
            parsed_number = phonenumbers.parse(match_text, locale)
            return phonenumbers.is_valid_number(parsed_number)

        except phonenumbers.NumberParseException:
            return False
