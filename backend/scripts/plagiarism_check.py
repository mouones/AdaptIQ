"""
plagiarism_check.py — Extract plain text from LaTeX rapport chapters,
chunk it into ~1000 word segments, and open each in free plagiarism checkers.

Usage:
    python scripts/plagiarism_check.py              # extract + open browser
    python scripts/plagiarism_check.py --extract     # just extract text files
    python scripts/plagiarism_check.py --report      # generate summary report
"""

import re, os, sys, json, webbrowser, urllib.parse
from pathlib import Path
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────
RAPPORT_DIR = Path(r"C:\Users\mns\Desktop\mw\rapport\mo2nes")
CHAPTERS_DIR = RAPPORT_DIR / "Chapitres"
OUTPUT_DIR = Path(r"C:\Users\mns\Desktop\mw\rapport\mo2nes\plagiarism_texts")
CHUNK_SIZE = 900  # words per chunk (free tools limit ~1000)

# Free plagiarism checker sites (no signup required)
CHECKERS = [
    {
        "name": "SmallSEOTools",
        "url": "https://smallseotools.com/plagiarism-checker/",
        "method": "manual",  # paste text manually
        "word_limit": 1000,
    },
    {
        "name": "DupliChecker",
        "url": "https://www.duplichecker.com/",
        "method": "manual",
        "word_limit": 1000,
    },
    {
        "name": "PaperRater",
        "url": "https://www.paperrater.com/plagiarism_checker",
        "method": "manual",
        "word_limit": 1500,
    },
    {
        "name": "Quetext",
        "url": "https://www.quetext.com/plagiarism-checker",
        "method": "manual",
        "word_limit": 2500,
    },
    {
        "name": "Plagiarism Detector",
        "url": "https://plagiarismdetector.net/",
        "method": "manual",
        "word_limit": 1000,
    },
]

# ── LaTeX -> Plain Text ────────────────────────────────────────────────────────

def strip_latex(text: str) -> str:
    """Convert LaTeX source to readable plain text."""
    # Remove comments
    text = re.sub(r'%.*$', '', text, flags=re.MULTILINE)
    # Remove \begin{...} and \end{...} for non-content environments
    text = re.sub(r'\\begin\{(lstlisting|figure|table|tabular|itemize|enumerate|center)\}.*?\\end\{\1\}',
                  '', text, flags=re.DOTALL)
    # Remove \includegraphics, \caption, \label, \ref, etc.
    text = re.sub(r'\\(includegraphics|caption|label|ref|cite|bibliography|bibliographystyle|nocite|FloatBarrier|clearpage|phantomsection|addcontentsline|renewcommand|begingroup|endgroup|let|pagenumbering|markboth|vspace|hspace|fontsize|selectfont|textwidth|textheight|linewidth)\b[^}]*\}?(\{[^}]*\})*', '', text)
    # Remove \section, \subsection etc. but keep the title text
    text = re.sub(r'\\(chapter|section|subsection|subsubsection)\*?\{([^}]*)\}', r'\2\n\n', text)
    # Remove formatting commands but keep content
    text = re.sub(r'\\(textbf|textit|emph|texttt|underline)\{([^}]*)\}', r'\2', text)
    # Remove \item
    text = re.sub(r'\\item\s*', '• ', text)
    # Remove remaining LaTeX commands
    text = re.sub(r'\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})*', '', text)
    # Remove braces
    text = re.sub(r'[{}]', '', text)
    # Remove $...$ math
    text = re.sub(r'\$[^$]*\$', '', text)
    # Clean up multiple newlines and spaces
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'^\s+$', '', text, flags=re.MULTILINE)
    return text.strip()


def extract_chapters() -> dict:
    """Extract plain text from all chapters."""
    chapters = {}
    
    # Introduction
    intro_path = RAPPORT_DIR / "introduction.tex"
    if intro_path.exists():
        raw = intro_path.read_text(encoding="utf-8", errors="replace")
        chapters["00_introduction"] = strip_latex(raw)
    
    # Chapters 1-6
    for i in range(1, 7):
        ch_path = CHAPTERS_DIR / f"chapitre{i}.tex"
        if ch_path.exists():
            raw = ch_path.read_text(encoding="utf-8", errors="replace")
            chapters[f"ch{i}"] = strip_latex(raw)
    
    # Abstract
    abs_path = RAPPORT_DIR / "abstract.tex"
    if abs_path.exists():
        raw = abs_path.read_text(encoding="utf-8", errors="replace")
        chapters["abstract"] = strip_latex(raw)
    
    # Conclusion (from report.tex)
    report_path = RAPPORT_DIR / "report.tex"
    if report_path.exists():
        raw = report_path.read_text(encoding="utf-8", errors="replace")
        # Extract conclusion section
        match = re.search(r'General Conclusion.*?(?=%%|\\end\{document\})', raw, re.DOTALL)
        if match:
            chapters["conclusion"] = strip_latex(match.group())
    
    return chapters


def chunk_text(text: str, max_words: int = CHUNK_SIZE) -> list:
    """Split text into chunks of max_words."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunk = ' '.join(words[i:i + max_words])
        if len(chunk.split()) > 50:  # skip tiny chunks
            chunks.append(chunk)
    return chunks


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--extract"
    
    print("=" * 70)
    print("AdaptIQ Rapport — Plagiarism Check Preparation")
    print("=" * 70)
    
    # Extract text
    print("\n[1/3] Extracting plain text from LaTeX chapters...")
    chapters = extract_chapters()
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    all_chunks = []
    total_words = 0
    
    for name, text in chapters.items():
        word_count = len(text.split())
        total_words += word_count
        
        # Save full text
        txt_path = OUTPUT_DIR / f"{name}.txt"
        txt_path.write_text(text, encoding="utf-8")
        
        # Chunk it
        chunks = chunk_text(text)
        for ci, chunk in enumerate(chunks):
            chunk_name = f"{name}_chunk{ci+1}"
            chunk_path = OUTPUT_DIR / f"{chunk_name}.txt"
            chunk_path.write_text(chunk, encoding="utf-8")
            all_chunks.append({
                "name": chunk_name,
                "chapter": name,
                "chunk_index": ci + 1,
                "word_count": len(chunk.split()),
                "file": str(chunk_path),
            })
        
        print(f"  {name}: {word_count} words -> {len(chunks)} chunks")
    
    print(f"\n  Total: {total_words} words across {len(chapters)} sections -> {len(all_chunks)} chunks")
    
    # Save manifest
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "total_words": total_words,
        "total_chunks": len(all_chunks),
        "chunk_size": CHUNK_SIZE,
        "chapters": {name: len(text.split()) for name, text in chapters.items()},
        "chunks": all_chunks,
        "checkers": CHECKERS,
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n  Manifest saved: {manifest_path}")
    
    if mode == "--extract":
        print("\n[2/3] Text extracted. Files saved to:")
        print(f"  {OUTPUT_DIR}")
        print(f"\n  To check plagiarism, run: python scripts/plagiarism_check.py --open")
        print(f"  Or copy-paste from the .txt files into any checker.\n")
        
        # Print quick stats
        print("=" * 70)
        print("CHAPTER WORD COUNTS")
        print("=" * 70)
        for name, text in chapters.items():
            wc = len(text.split())
            bar = "#" * (wc // 100)
            print(f"  {name:20s} {wc:5d} words  {bar}")
        print(f"  {'TOTAL':20s} {total_words:5d} words")
        print("=" * 70)
        
        # Print checker recommendations
        print("\nRECOMMENDED FREE PLAGIARISM CHECKERS:")
        print("-" * 70)
        for c in CHECKERS:
            print(f"  • {c['name']:25s} {c['url']}")
            print(f"    Word limit: {c['word_limit']} words | Method: paste text")
        print("-" * 70)
        print("\nTIP: Use at least 2 different checkers for reliable results.")
        print("     Check the LONGEST chapters first (ch1, ch6, ch5).")
        print("     Free tools check against web content, not academic databases.")
        return
    
    if mode == "--open":
        print("\n[2/3] Opening plagiarism checker sites...")
        print("  Opening SmallSEOTools and DupliChecker...")
        webbrowser.open(CHECKERS[0]["url"])
        webbrowser.open(CHECKERS[1]["url"])
        
        print("\n[3/3] INSTRUCTIONS:")
        print(f"  1. Text chunks are saved in: {OUTPUT_DIR}")
        print(f"  2. Open each .txt file and paste into the checker")
        print(f"  3. Start with the LARGEST chapters: ch1, ch6, ch5")
        print(f"  4. Record results in the report below\n")
        return
    
    if mode == "--report":
        # Generate a report template
        report_path = OUTPUT_DIR / "plagiarism_report.md"
        report = [
            "# AdaptIQ Rapport — Plagiarism Check Report",
            f"\nGenerated: {datetime.now().isoformat()}",
            f"Total words checked: {total_words}",
            f"Chapters: {len(chapters)}",
            f"Chunks: {len(all_chunks)}",
            "\n## Results by Chapter\n",
            "| Chapter | Words | Checker Used | Unique % | Notes |",
            "|---------|-------|-------------|----------|-------|",
        ]
        for name, text in chapters.items():
            wc = len(text.split())
            report.append(f"| {name} | {wc} | _pending_ | _pending_ | |")
        
        report.extend([
            "\n## Summary\n",
            "- **Overall uniqueness:** _pending_",
            "- **Checkers used:** SmallSEOTools, DupliChecker",
            "- **Flagged sections:** _none yet_",
            "\n## Notes\n",
            "- Free checkers compare against web content only",
            "- Academic databases (Turnitin) not included",
            "- Technical terms and standard Scrum phrases may trigger false positives",
        ])
        
        report_path.write_text('\n'.join(report), encoding="utf-8")
        print(f"\n  Report template saved: {report_path}")
        return


if __name__ == "__main__":
    main()
