from dataclasses import dataclass


@dataclass
class MemoryConfig:
    recent_turns_in_context: int = 10
    thread_list_page_size: int = 20
    schema_version: int = 1
