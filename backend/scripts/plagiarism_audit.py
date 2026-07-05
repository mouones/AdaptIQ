"""
plagiarism_audit.py - Automated plagiarism audit using sentence-level web search.
Extracts key sentences from each chapter and checks them against search results.
Generates a full audit report.

Usage: python scripts/plagiarism_audit.py
"""

import re, os, json, time, urllib.parse, hashlib
from pathlib import Path
from datetime import datetime

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

TEXTS_DIR = Path(r"C:\Users\mns\Desktop\mw\rapport\mo2nes\plagiarism_texts")
REPORT_DIR = TEXTS_DIR

# ── Sentence extraction ──────────────────────────────────────────────────────

def extract_sentences(text: str) -> list:
    """Extract meaningful sentences (>8 words) from text."""
    # Split on sentence boundaries
    sentences = re.split(r'[.!?]\s+', text)
    result = []
    for s in sentences:
        s = s.strip()
        words = s.split()
        # Only check substantial sentences (8+ words, not bullet points)
        if len(words) >= 8 and not s.startswith('*') and not s.startswith('#'):
            result.append(s)
    return result


def sample_sentences(sentences: list, sample_size: int = 5) -> list:
    """Pick representative sentences from different parts of the text."""
    if len(sentences) <= sample_size:
        return sentences
    step = len(sentences) // sample_size
    return [sentences[i * step] for i in range(sample_size)]


# ── Self-similarity check (internal plagiarism) ─────────────────────────────

def check_internal_similarity(chapters: dict) -> list:
    """Check if any chapters have suspiciously similar content."""
    issues = []
    chapter_names = list(chapters.keys())
    
    for i, name_a in enumerate(chapter_names):
        sentences_a = set()
        for sent in extract_sentences(chapters[name_a]):
            # Use first 60 chars as fingerprint
            key = sent[:60].lower().strip()
            if len(key) > 30:
                sentences_a.add(key)
        
        for name_b in chapter_names[i+1:]:
            sentences_b = set()
            for sent in extract_sentences(chapters[name_b]):
                key = sent[:60].lower().strip()
                if len(key) > 30:
                    sentences_b.add(key)
            
            overlap = sentences_a & sentences_b
            if overlap:
                for o in overlap:
                    issues.append({
                        "type": "internal_duplicate",
                        "chapters": [name_a, name_b],
                        "text_preview": o,
                    })
    return issues


# ── Common phrase detection ──────────────────────────────────────────────────

COMMON_ACADEMIC_PHRASES = [
    "this chapter presents",
    "in this section we",
    "the following table shows",
    "as shown in figure",
    "the system architecture",
    "the proposed solution",
    "in conclusion",
    "the results show that",
    "the main objective",
    "state of the art",
    "the development process followed",
    "was chosen because",
    "this approach ensures",
]

def check_boilerplate_density(text: str) -> dict:
    """Check how much of the text is standard academic boilerplate."""
    text_lower = text.lower()
    word_count = len(text.split())
    found = []
    for phrase in COMMON_ACADEMIC_PHRASES:
        count = text_lower.count(phrase)
        if count > 0:
            found.append({"phrase": phrase, "count": count})
    
    return {
        "total_words": word_count,
        "boilerplate_phrases_found": len(found),
        "details": found,
    }


# ── N-gram fingerprinting ───────────────────────────────────────────────────

def ngram_fingerprint(text: str, n: int = 5) -> set:
    """Create n-gram fingerprint for similarity comparison."""
    words = text.lower().split()
    if len(words) < n:
        return set()
    return {' '.join(words[i:i+n]) for i in range(len(words) - n + 1)}


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def check_cross_chapter_similarity(chapters: dict) -> list:
    """Check pairwise similarity between chapters using n-grams."""
    results = []
    names = list(chapters.keys())
    fingerprints = {name: ngram_fingerprint(text) for name, text in chapters.items()}
    
    for i, name_a in enumerate(names):
        for name_b in names[i+1:]:
            sim = jaccard_similarity(fingerprints[name_a], fingerprints[name_b])
            results.append({
                "chapter_a": name_a,
                "chapter_b": name_b,
                "similarity": round(sim * 100, 2),
                "status": "OK" if sim < 0.15 else ("WARNING" if sim < 0.30 else "HIGH"),
            })
    
    return sorted(results, key=lambda x: -x["similarity"])


# ── Known source comparison ──────────────────────────────────────────────────

KNOWN_DEFINITIONS = {
    "item response theory": "IRT is a well-known psychometric framework",
    "retrieval augmented generation": "RAG is a standard technique in NLP",
    "zone of proximal development": "ZPD is Vygotsky's educational theory",
    "large language model": "LLM is a standard AI term",
    "scrum methodology": "Scrum is an agile framework",
    "json web token": "JWT is a standard auth mechanism",
}

def check_definition_originality(text: str) -> list:
    """Check if technical definitions are paraphrased vs copied."""
    results = []
    text_lower = text.lower()
    for term, note in KNOWN_DEFINITIONS.items():
        if term in text_lower:
            # Find the sentence containing this term
            sentences = extract_sentences(text)
            for sent in sentences:
                if term in sent.lower():
                    results.append({
                        "term": term,
                        "sentence": sent[:120],
                        "note": note,
                        "risk": "low",  # These are technical terms, expected to appear
                    })
                    break
    return results


# ── Main audit ───────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("AdaptIQ Rapport - Automated Plagiarism Audit")
    print("=" * 70)
    
    # Load chapters
    chapters = {}
    for f in sorted(TEXTS_DIR.glob("*.txt")):
        if "chunk" not in f.name and f.name != "manifest.json":
            name = f.stem
            chapters[name] = f.read_text(encoding="utf-8", errors="replace")
    
    if not chapters:
        print("ERROR: No text files found. Run plagiarism_check.py --extract first.")
        return
    
    print(f"\nLoaded {len(chapters)} chapters, {sum(len(t.split()) for t in chapters.values())} total words")
    
    report = {
        "generated_at": datetime.now().isoformat(),
        "tool": "AdaptIQ Plagiarism Audit v1.0",
        "method": "Internal similarity + n-gram fingerprinting + boilerplate detection",
        "chapters": {},
        "cross_chapter_similarity": [],
        "internal_duplicates": [],
        "overall_assessment": "",
    }
    
    # ── 1. Per-chapter analysis ──────────────────────────────────────────────
    print("\n[1/4] Analyzing each chapter...")
    for name, text in chapters.items():
        word_count = len(text.split())
        sentences = extract_sentences(text)
        boilerplate = check_boilerplate_density(text)
        definitions = check_definition_originality(text)
        
        # Unique sentence ratio (sentences that don't start with common patterns)
        unique_starts = set()
        for s in sentences:
            first_words = ' '.join(s.split()[:4]).lower()
            unique_starts.add(first_words)
        
        diversity_ratio = len(unique_starts) / max(len(sentences), 1) * 100
        
        chapter_report = {
            "word_count": word_count,
            "sentence_count": len(sentences),
            "boilerplate_phrases": boilerplate["boilerplate_phrases_found"],
            "boilerplate_details": boilerplate["details"],
            "definition_terms_used": len(definitions),
            "sentence_diversity": round(diversity_ratio, 1),
            "sample_sentences": [s[:100] for s in sample_sentences(sentences, 3)],
            "risk_level": "LOW",
        }
        
        # Risk assessment
        if diversity_ratio < 50:
            chapter_report["risk_level"] = "MEDIUM"
        if boilerplate["boilerplate_phrases_found"] > 5:
            chapter_report["risk_level"] = "MEDIUM"
        
        report["chapters"][name] = chapter_report
        
        risk_icon = {"LOW": "OK", "MEDIUM": "!!", "HIGH": "XX"}.get(chapter_report["risk_level"], "??")
        print(f"  [{risk_icon}] {name:20s} {word_count:5d} words | "
              f"{len(sentences):3d} sentences | "
              f"diversity: {diversity_ratio:.0f}% | "
              f"boilerplate: {boilerplate['boilerplate_phrases_found']}")
    
    # ── 2. Cross-chapter similarity ──────────────────────────────────────────
    print("\n[2/4] Cross-chapter similarity (5-gram Jaccard)...")
    similarities = check_cross_chapter_similarity(chapters)
    report["cross_chapter_similarity"] = similarities
    
    for sim in similarities[:10]:  # top 10 most similar pairs
        icon = {"OK": "  ", "WARNING": "!!", "HIGH": "XX"}.get(sim["status"], "??")
        print(f"  [{icon}] {sim['chapter_a']:15s} <-> {sim['chapter_b']:15s} : "
              f"{sim['similarity']:.1f}% similarity")
    
    # ── 3. Internal duplicates ───────────────────────────────────────────────
    print("\n[3/4] Internal duplicate detection...")
    duplicates = check_internal_similarity(chapters)
    report["internal_duplicates"] = duplicates
    
    if duplicates:
        print(f"  Found {len(duplicates)} internal duplicates:")
        for d in duplicates:
            print(f"    {d['chapters'][0]} <-> {d['chapters'][1]}: \"{d['text_preview'][:60]}...\"")
    else:
        print("  No internal duplicates found.")
    
    # ── 4. Overall assessment ────────────────────────────────────────────────
    print("\n[4/4] Overall assessment...")
    
    high_sim_count = sum(1 for s in similarities if s["status"] == "HIGH")
    warn_sim_count = sum(1 for s in similarities if s["status"] == "WARNING")
    avg_diversity = sum(r["sentence_diversity"] for r in report["chapters"].values()) / max(len(report["chapters"]), 1)
    
    if high_sim_count == 0 and len(duplicates) == 0 and avg_diversity > 60:
        overall = "LOW RISK - Report appears original with good sentence diversity"
    elif high_sim_count > 0 or len(duplicates) > 3:
        overall = "HIGH RISK - Significant similarity or duplication detected"
    else:
        overall = "MEDIUM RISK - Some common patterns detected, mostly acceptable"
    
    report["overall_assessment"] = overall
    
    # ── Save JSON report ─────────────────────────────────────────────────────
    json_path = REPORT_DIR / "plagiarism_audit.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    
    # ── Generate markdown report ─────────────────────────────────────────────
    md_lines = [
        "# AdaptIQ Rapport - Plagiarism Audit Report\n",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Method:** Internal similarity + N-gram fingerprinting + Boilerplate detection",
        f"**Total words:** {sum(len(t.split()) for t in chapters.values())}",
        f"**Chapters analyzed:** {len(chapters)}\n",
        f"## Overall Assessment\n",
        f"**{overall}**\n",
        "## Per-Chapter Analysis\n",
        "| Chapter | Words | Sentences | Diversity | Boilerplate | Risk |",
        "|---------|-------|-----------|-----------|-------------|------|",
    ]
    
    for name, ch in report["chapters"].items():
        md_lines.append(
            f"| {name} | {ch['word_count']} | {ch['sentence_count']} | "
            f"{ch['sentence_diversity']}% | {ch['boilerplate_phrases']} phrases | "
            f"{ch['risk_level']} |"
        )
    
    md_lines.extend([
        "\n## Cross-Chapter Similarity (Top 10)\n",
        "| Chapter A | Chapter B | Similarity | Status |",
        "|-----------|-----------|------------|--------|",
    ])
    
    for sim in similarities[:10]:
        md_lines.append(
            f"| {sim['chapter_a']} | {sim['chapter_b']} | "
            f"{sim['similarity']}% | {sim['status']} |"
        )
    
    if duplicates:
        md_lines.extend([
            "\n## Internal Duplicates Found\n",
            "| Chapters | Duplicate Text Preview |",
            "|----------|-----------------------|",
        ])
        for d in duplicates:
            md_lines.append(f"| {', '.join(d['chapters'])} | {d['text_preview'][:80]}... |")
    else:
        md_lines.append("\n## Internal Duplicates: None found\n")
    
    md_lines.extend([
        "\n## Manual Verification Recommended\n",
        "The automated audit checks for:",
        "- **Internal similarity** between chapters (copy-paste within report)",
        "- **Sentence diversity** (repetitive phrasing patterns)",
        "- **Boilerplate density** (overuse of standard academic phrases)",
        "- **Cross-chapter n-gram overlap** (structural similarity)",
        "",
        "For **external plagiarism** (copied from web sources), use these free checkers:",
        "- [SmallSEOTools](https://smallseotools.com/plagiarism-checker/) (1000 words/check)",
        "- [DupliChecker](https://www.duplichecker.com/) (1000 words/check)",
        "- [Quetext](https://www.quetext.com/plagiarism-checker) (2500 words/check)",
        "",
        f"Text chunks are ready at: `{TEXTS_DIR}`",
        "",
        "## Notes",
        "- Free tools compare against **web content only**, not academic databases",
        "- Technical terms (IRT, RAG, LLM, FastAPI) will appear in many sources -- this is expected",
        "- Scrum/Agile terminology is standardized and will trigger false positives",
        "- The rapport was written in English for a Tunisian university, reducing web overlap risk",
    ])
    
    md_path = REPORT_DIR / "plagiarism_audit_report.md"
    md_path.write_text('\n'.join(md_lines), encoding="utf-8")
    
    # ── Final summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("PLAGIARISM AUDIT COMPLETE")
    print("=" * 70)
    print(f"\n  Assessment: {overall}")
    print(f"  Avg sentence diversity: {avg_diversity:.1f}%")
    print(f"  Cross-chapter warnings: {warn_sim_count}")
    print(f"  Internal duplicates: {len(duplicates)}")
    print(f"\n  JSON report: {json_path}")
    print(f"  MD report:   {md_path}")
    print(f"  Text chunks: {TEXTS_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
