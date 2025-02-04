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

def format_row(context_id: str, title: str, name: str, update_time: str,
               ID_LENGTH: int, TITLE_MAX_LENGTH: int, NAME_LENGTH: int) -> str:
   # Truncate title if needed and pad with spaces
   title_display = (title[:TITLE_MAX_LENGTH-3] + "...") if len(title) > TITLE_MAX_LENGTH else title
   title_display = title_display.ljust(TITLE_MAX_LENGTH)

   # Pad other fields
   context_id = context_id.ljust(ID_LENGTH)
   name = name.ljust(NAME_LENGTH)

   return f"{context_id}  {title_display}  {name}  {update_time}\n"

def format_rows(contexts) -> str:
   ID_LENGTH = 10
   TITLE_MAX_LENGTH = 41
   NAME_LENGTH = 10
   DATE_LENGTH = 19      # YYYY-MM-DD HH:MM:SS

   header = format_row("CONTEXT_ID", "TITLE", "NAME", "UPDATE_TIME", 
                            ID_LENGTH, TITLE_MAX_LENGTH, NAME_LENGTH)

   output = header
   for context in contexts:
       output += format_row(
           context["context_id"],
           context["title"] or "",
           context["name"] or "",
           context["update_time"],
           ID_LENGTH,
           TITLE_MAX_LENGTH,
           NAME_LENGTH
       )
   return output.rstrip()
