class Formatting:
    SEPARATOR = "----"
    NEWLINES = "\n\n"
    SECTION_TEMPLATE = "{separator}{name}:{newlines}{content}{newlines}"

    @classmethod
    def format(cls, name, content):
        return cls.SECTION_TEMPLATE.format(
            separator=cls.SEPARATOR,
            name=name,
            content=content,
            newlines=cls.NEWLINES
        )

