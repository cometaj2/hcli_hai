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

# Format a single row with fixed-width columns for context_id, update_time, and name.
# The title is output in full without any width constraints or trailing dots.
def format_frontmatter_row(skill_id, description):

    # Fixed column widths
    id_width = 32

    # Format fixed columns
    id_formatted = skill_id[:id_width].ljust(id_width)

    # Output the row with full title (no truncation or trailing dots)
    return f"{id_formatted}  {description}"

# Format multiple context rows with a header.
# The title column is not constrained and has no trailing dots.
def format_frontmatter_rows(contexts):

    # Create header
    header = format_frontmatter_row("SKILL_ID", "DESCRIPTION")

    # Format each row
    rows = [header]
    for ctx in contexts:
        row = format_frontmatter_row(
            ctx.get("skill_id", ""),
            ctx.get("description", "")
        )
        rows.append(row)

    # Join rows with newlines
    return "\n".join(rows)

def format_artifact_row(artifact_id, filename):
    """Format a single artifact row for text output."""
    artifact_id_width = 7

    artifact_id = "" if artifact_id is None else str(artifact_id)[:7]
    filename = "" if filename is None else str(filename)

    artifact_id_formatted = artifact_id[:artifact_id_width].ljust(
        artifact_id_width
    )

    return f"{artifact_id_formatted}  {filename}"


def format_artifact_rows(artifacts, output_format="text"):
    """
    Format artifact information.

    Text output:
        ARTIFACT_ID                             FILENAME
        abc123...                               example.txt

    Dictionary output:
        {
            "artifacts": [
                {
                    "artifact_id": "...",
                    "filename": "example.txt"
                }
            ]
        }
    """
    artifact_data = [
        {
            "artifact_id": artifact.get("artifact_id", ""),
            "filename": artifact.get("filename", ""),
        }
        for artifact in artifacts
    ]

    if output_format == "dict":
        return {
            "artifacts": artifact_data,
        }

    if output_format != "text":
        raise ValueError("output_format must be 'text' or 'dict'")

    rows = [
        format_artifact_row("ID", "FILENAME")
    ]

    rows.extend(
        format_artifact_row(
            artifact["artifact_id"],
            artifact["filename"],
        )
        for artifact in artifact_data
    )

    return "\n".join(rows)
