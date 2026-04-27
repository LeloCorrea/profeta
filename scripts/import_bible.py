import json
from pathlib import Path

# 📥 Arquivo de entrada (o que você baixou)
INPUT_PATH = Path("data/bible/biblialivrecorrecao1.json")

# 📤 Arquivo final pro bot
OUTPUT_PATH = Path("data/bible/bible.json")


def transform():
    print("📖 Lendo arquivo da bíblia...")

    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {INPUT_PATH}")

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    verses = []

    print("🔄 Convertendo estrutura...")

    for item in data:
        # Ignora metadado
        if "capitulos" not in item:
            continue

        book_name = item.get("nome", "").strip()

        for chapter_index, chapter in enumerate(item["capitulos"], start=1):
            for verse_index, verse_text in enumerate(chapter, start=1):
                text = str(verse_text).strip()

                if not text:
                    continue

                verses.append({
                    "book": book_name,
                    "chapter": chapter_index,
                    "verse": verse_index,
                    "text": text
                })

    print(f"✅ Total de versículos convertidos: {len(verses)}")

    print("💾 Salvando arquivo final...")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(verses, f, ensure_ascii=False, indent=2)

    print(f"📁 Arquivo salvo em: {OUTPUT_PATH}")


if __name__ == "__main__":
    transform()
