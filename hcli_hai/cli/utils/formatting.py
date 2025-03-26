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

# Format a single row with fixed-width columns for context_id, update_time, and name.
# The title is output in full without any width constraints or trailing dots.
def format_row(context_id, update_time, name, title):

    # Fixed column widths
    id_width = 10
    time_width = 19
    name_width = 10

    # Format fixed columns
    id_formatted = context_id[:id_width].ljust(id_width)
    time_formatted = update_time[:time_width].ljust(time_width)
    name_formatted = name[:name_width].ljust(name_width)

    # Output the row with full title (no truncation or trailing dots)
    return f"{id_formatted}  {time_formatted}  {name_formatted}  {title}"

# Format multiple context rows with a header.
# The title column is not constrained and has no trailing dots.
def format_rows(contexts):

    # Create header
    header = format_row("CONTEXT_ID", "UPDATE_TIME", "NAME", "TITLE")

    # Format each row
    rows = [header]
    for ctx in contexts:
        row = format_row(
            ctx.get("context_id", ""),
            ctx.get("update_time", ""),
            ctx.get("name", ""),
            ctx.get("title", "")
        )
        rows.append(row)

    # Join rows with newlines
    return "\n".join(rows)
