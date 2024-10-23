import re

from collections import defaultdict
from heapq import nlargest
from typing import List, Dict


class AdvancedTitleGenerator:
    def __init__(self):
        pass

    def preprocess_text(self, text: str) -> str:
        # Remove special characters and convert to lowercase
        return re.sub(r'[^\w\s]', '', text.lower())

    def get_sentences(self, text: str) -> List[str]:
        # Simple sentence tokenization
        return re.split(r'(?<=[.!?])\s+', text)

    def calculate_word_frequencies(self, text: str) -> Dict[str, int]:
        words = text.split()
        word_freq = defaultdict(int)
        for word in words:
            word_freq[word] += 1
        return word_freq

    def score_sentences(self, sentences: List[str], word_freq: Dict[str, int]) -> Dict[int, float]:
        sentence_scores = defaultdict(float)
        for i, sentence in enumerate(sentences):
            for word in self.preprocess_text(sentence).split():
                if word in word_freq:
                    sentence_scores[i] += word_freq[word]
        return sentence_scores

    def generate_summary(self, text: str, num_sentences: int = 1) -> str:
        sentences = self.get_sentences(text)
        preprocessed_text = self.preprocess_text(text)
        word_freq = self.calculate_word_frequencies(preprocessed_text)
        sentence_scores = self.score_sentences(sentences, word_freq)

        summary_sentences = nlargest(num_sentences, sentence_scores, key=sentence_scores.get)
        summary = ' '.join([sentences[i] for i in sorted(summary_sentences)])
        return summary

    # produces a summary then a title for the current context that's at most 10 words long from a limited bit of conversation context.
    def generate_title(self, text: str, max_words: int = 20) -> str:
        # Generate a summary
        summary = self.generate_summary(text, num_sentences=1)

        # Create a title from the summary
        words = summary.split()[:max_words]
        title = " ".join(words)
        if len(summary.split()) > max_words:
            title += "..."

        return title
