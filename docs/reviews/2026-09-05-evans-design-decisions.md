# Evans reading and OptionsLab delivery decisions

Source: Eric Evans, [Domain-Driven Design, final manuscript April 15, 2003](https://fabiofumarola.github.io/nosql/readingMaterial/Evans03.pdf), 359 pages. The local copy in the macOS collector repository and Downloads had identical SHA-256 `5b4ee3616251cdb15930f944227d109b0b327876eb64e7070cc8f4a85b7b98a8`.

Before implementation, the primary agent read extracted pages 1–88; three reviewers read every page in contiguous ranges 89–169, 170–278 and 279–359. Thus the team covered the complete manuscript, including examples, conclusions, glossary, references and appendix. Truncated extraction outputs were reread. Selected rendered diagrams were checked separately; this is not a claim that every figure was visually inspected. Extraction used pypdf 6.17.0; selected-page rendering used pypdfium2 5.13.0 and Pillow 12.3.0 in a temporary reader environment. The PDF and extracted book text are not repository artifacts.

Relevant guidance, paraphrased from those pages:

- Pages 18–22, 25–34 and 37–48: make domain knowledge explicit, use consistent terms, and let a tested implementation refine the model. Documents should explain intent instead of duplicating code.
- Pages 60–82 and 127–128: distinguish identity from values and meaningful operations; organize modules around concepts rather than pattern categories or technical tiers.
- Pages 89–104 and 175–191: define invariant boundaries, preserve identity during reconstruction, and separate calculations from state changes.
- Pages 238–260: maintain consistent meanings within contexts and translate external models explicitly; shared code alone is not necessarily a Shared Kernel.
- Pages 281–311 and 330–351: keep the core focused, introduce structure only when useful, preserve operational facts that violate policy, and learn through working releases.

Our project decisions (application of the reading, not rules prescribed by the book):

1. Begin with one-contract premium affordability and hand-checked boundary examples. Its result is evidence, not authorization or a funds reservation.
2. Add types alongside the behavior that consumes them. The roadmap's `domain.py` is a logical vocabulary, not an all-schemas-first requirement. Retain all approved financial and recovery semantics.
3. Use ordinary functions, frozen values and standard-library persistence. Introduce no DDD framework, universal repository, rule DSL or message bus.
4. Keep OptionsLab's policy model separate from LEAN/Alpaca representations through small semantic translators. Preserve unexpected holdings and late fills for recovery.
5. Separate research evidence, adopted contracts and implementation into review units. Aim for a few hundred changed lines per behavior; split changes nearing 1,000 lines unless predominantly meaningful tests. The numeric preference comes from the user, not Evans.
