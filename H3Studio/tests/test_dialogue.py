import unittest

from dialogue import format_dialogue


class DialogueFormattingTests(unittest.TestCase):
    def test_soft_spoken_line_in_sound_effect_description_is_tagged(self):
        for cue in ('清冷低語', '低语'):
            text = f'* **音效 [無 BGM]**：風聲「呼——」，少年{cue}：「請等我回來。」。'
            expected = f'* **音效 [無 BGM]**：風聲「呼——」，少年{cue}：<d>[Chinese] 請等我回來。</d>。'
            self.assertEqual(format_dialogue(text), expected)
            self.assertEqual(format_dialogue(expected), expected)

    def test_silent_directions_and_visible_low_voice_quotes_remain_unchanged(self):
        for text in ('少年不要低語：「秘密。」', '少年不低语：「秘密。」', '字幕顯示低語：「秘密。」'):
            self.assertEqual(format_dialogue(text), text)

    def test_quoted_negative_answer_is_spoken_not_a_no_dialogue_option(self):
        self.assertEqual(format_dialogue('小明說：「沒有。」'), '小明說：<d>[Chinese] 沒有。</d>')
        self.assertEqual(format_dialogue('無', dialogue_field=True), '無')

    def test_nested_quotes_and_newlines_preserve_words(self):
        text = '小明說：「他說『再見』，\n但還會回來。」'
        expected = '小明說：<d>[Chinese] 他說『再見』，\n但還會回來。</d>'
        self.assertEqual(format_dialogue(text), expected)
        self.assertEqual(format_dialogue(expected), expected)

    def test_existing_tag_content_is_opaque_even_when_it_contains_speech_labels(self):
        text = '<d>[Chinese] 小明台詞：這句也是台詞。\n旁白：「故事開始。」</d>'
        self.assertEqual(format_dialogue(text), text)

    def test_existing_untagged_language_is_added_once(self):
        self.assertEqual(format_dialogue('<d>你好！</d>'), '<d>[Chinese] 你好！</d>')
        self.assertEqual(format_dialogue('<d></d>'), '<d></d>')

    def test_known_speaker_shorthand_and_explicit_language_hint(self):
        self.assertEqual(format_dialogue('小明：「你好！」', speakers=['小明']), '小明：<d>[Chinese] 你好！</d>')
        self.assertEqual(format_dialogue('旁白（法語）：「Bonjour !」'), '旁白（法語）：<d>[French] Bonjour !</d>')

    def test_dedicated_field_and_bare_labeled_dialogue(self):
        self.assertEqual(format_dialogue('「你好！」', dialogue_field=True), '<d>[Chinese] 你好！</d>')
        self.assertEqual(format_dialogue('**小明台詞：**你好！'), '**小明台詞：**<d>[Chinese] 你好！</d>')
        self.assertEqual(format_dialogue('台詞：[English] Hello!'), '台詞：<d>[English] Hello!</d>')

    def test_japanese_and_korean_are_not_mislabeled_chinese(self):
        self.assertEqual(format_dialogue('旁白：「明日、行きます。」'), '旁白：<d>[Japanese] 明日、行きます。</d>')
        self.assertEqual(format_dialogue('旁白：「안녕하세요。」'), '旁白：<d>[Korean] 안녕하세요。</d>')

    def test_unclosed_quote_is_not_invented_or_removed(self):
        text = '小明說：「你好！\n鏡頭移向窗外。'
        self.assertEqual(format_dialogue(text), text)

    def test_label_on_previous_line_only_marks_the_immediate_plain_line(self):
        text = '**小明台詞：**\n你好，歡迎回來！\n鏡頭移向窗外。'
        self.assertEqual(format_dialogue(text), '**小明台詞：**\n<d>[Chinese] 你好，歡迎回來！</d>\n鏡頭移向窗外。')
        for following in ('鏡頭：拉遠', '### 下一鏡', '\n畫面移向窗外。'):
            text = '**小明台詞：**\n' + following
            self.assertEqual(format_dialogue(text), text)

    def test_multiline_visible_quote_is_not_parsed_as_speech(self):
        text = '招牌寫著「\n台詞：歡迎光臨\n」。'
        self.assertEqual(format_dialogue(text), text)

    def test_english_sentence_before_dialogue_does_not_set_spoken_language(self):
        text = 'The room has Chinese signage. Alex says: "Welcome home!"'
        self.assertEqual(format_dialogue(text), 'The room has Chinese signage. Alex says: <d>[English] Welcome home!</d>')


if __name__ == '__main__':
    unittest.main()
