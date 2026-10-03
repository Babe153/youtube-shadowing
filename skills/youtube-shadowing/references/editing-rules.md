# Faithful editing for shadowing

Read the source chunk as spoken material. Keep every word in its original order.
Only add punctuation, sentence capitalization and sentence boundaries. Keep
contractions, filler words, false starts and repetitions: the learner follows the
audio, so fluent paraphrases are harmful here.

Do not correct spelling, grammar, names, numbers or apparent ASR errors from
context alone. Set `uncertain: true` on a sentence whose wording looks suspect,
without replacing or deleting the suspect words. Sound-only Music/Applause/
Laughter cues are removed by the parser; do not independently delete other text.

Use complete spoken sentences where possible. A sentence may occupy several
printed lines. Do not fragment sentences merely to make the PDF visually short.

`context_before` and `context_after` are for boundaries only. They are not part
of the text you must output. When a chunk starts midway through a sentence, set
`join_previous: true` on its first sentence. Retain the capitalization appropriate
for the continuation. The assembler joins it to the preceding chunk's last
sentence. Never repeat context words. If the previous chunk ended the sentence
too early, this join removes its terminal period, question mark or similar mark.

Each `sentences` entry contains one paragraph. Output JSON only into the result
file, without code fences, commentary, headers or timestamps. Copy `chunk_id` and
`chunk_hash` exactly. The script computes word ranges; you do not need to count
tokens. See data-contract.md for the small output schema.

Validation catches additions, omissions and changed words. It permits case and
ordinary punctuation changes. It does not decide whether punctuation is correct
or whether the subtitle accurately matches the recording. Review those choices
with the best judgment available from the source.
