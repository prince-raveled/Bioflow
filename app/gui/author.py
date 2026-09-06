"""The author's signature, carried the way this application carries everything else.

BioFlow reads nucleotide sequence for a living, so the person who wrote it is
recorded in the same alphabet rather than in a plain "about" box. The strings
below are encoded to DNA at import and decoded again on demand, which is what
makes the sequence in the interface real: it is not decorative letters chosen to
look like a genome, it is the contact details themselves, and decoding it gives
them back exactly.

Two bits per base, four bases per character - the same packing a FASTA file
would use to store an alphabet of four symbols.
"""

from __future__ import annotations

#: The address book. One place, so the interface never restates it.
AUTHOR_NAME = "Prince Kumar"
AUTHOR_EMAIL = "princeatspace@gmail.com"
#: The share link carries tracking parameters that identify how it was sent;
#: the profile itself is everything before the "?".
AUTHOR_LINKEDIN = "https://www.linkedin.com/in/prince-kumar-392b28353"

#: Canonical order. Reversing the mapping is how decoding works, so the two
#: directions cannot disagree.
BASES = ("A", "C", "G", "T")
_VALUES = {base: index for index, base in enumerate(BASES)}

#: Read like a FASTA description line, because that is what it is. Kept short
#: enough to sit in the sidebar without being cut off, which reads as a bug
#: rather than as the truncation a sequence viewer would do deliberately.
FASTA_HEADER = f">{AUTHOR_NAME.lower().replace(' ', '_')} | BioFlow"


def encode(text: str) -> str:
    """Pack text into nucleotides, two bits per base.

    Each byte becomes four bases, most significant pair first, so the sequence
    length is always four times the byte length and decoding needs no delimiter.
    """
    sequence = []
    for byte in text.encode("utf-8"):
        for shift in (6, 4, 2, 0):
            sequence.append(BASES[(byte >> shift) & 0b11])
    return "".join(sequence)


def decode(sequence: str) -> str:
    """Recover the text a sequence was packed from.

    Raises ValueError on anything that is not a whole number of complete
    codons of valid bases, rather than returning plausible nonsense.
    """
    cleaned = "".join(sequence.split()).upper()
    if len(cleaned) % 4:
        raise ValueError("A packed sequence is a whole number of four-base groups.")
    unknown = sorted(set(cleaned) - set(BASES))
    if unknown:
        raise ValueError(f"Not a nucleotide sequence: {', '.join(unknown)}")
    payload = bytearray()
    for index in range(0, len(cleaned), 4):
        byte = 0
        for base in cleaned[index:index + 4]:
            byte = (byte << 2) | _VALUES[base]
        payload.append(byte)
    return payload.decode("utf-8")


def contact_sequence() -> str:
    """The whole address book as one strand.

    A tab separates the fields, so one decode returns both and the widget does
    not need a second encoding scheme to tell them apart.
    """
    return encode(f"{AUTHOR_EMAIL}\t{AUTHOR_LINKEDIN}")


def decode_contact(sequence: str) -> tuple[str, str]:
    """The email and profile a contact strand carries."""
    email, _, profile = decode(sequence).partition("\t")
    return email, profile


def wrapped(sequence: str, width: int = 60) -> list[str]:
    """Split a sequence into FASTA-width lines."""
    return [sequence[index:index + width] for index in range(0, len(sequence), width)]
