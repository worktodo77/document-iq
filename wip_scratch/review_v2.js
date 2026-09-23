export const meta = {
  name: 'dociq-package-review-v2',
  description: 'Adversarial multi-lens review of one DocIQ package: find (+optional corpus measurement), dedupe, refute, write',
  phases: [
    { title: 'Find', detail: 'three Opus reviewers by lens group, plus an optional counts-only corpus measurement' },
    { title: 'Dedupe', detail: 'merge duplicate findings before verification' },
    { title: 'Refute', detail: 'one Opus skeptic per distinct A/B finding' },
    { title: 'Write', detail: 'findings markdown returned as text' },
  ],
}

const SP = 'C:\\Users\\Alex\\AppData\\Local\\Temp\\claude\\C--Users-Alex\\c4101d6e-425a-4e46-b1e2-205e8e47cc10\\scratchpad\\recovered'
const pkg = args.package
const TEMPLATE = `${SP}\\review_brief_template.md`
const SECTION = `${SP}\\${args.section}`
const OUT = `${SP}\\review_${pkg}`

const GROUPS = [
  { key: 'wiring', lenses: 'lens 1 (regressions in other subsystems), lens 5 (determinism, fresh vs resumed), and every "default path" / entry-point risk the package section names' },
  { key: 'spec', lenses: 'lens 2 (specification conformance, every exact wording), lens 6 (disclosure truth: every note, every path yielding less evidence), lens 7 (client data, D-12), and every prose CLAIM the package adds (docstrings, amendment prose, help text, register-facing text) checked against the decision register' },
  { key: 'tests', lenses: 'lens 3 (test strength: for each new or changed test, could it pass while the behaviour is wrong; diff every changed test against its base; run mutation checks on a COPY of the worktree) and lens 4 (class siblings: enumerate and probe each)' },
]

const FIND_SCHEMA = {
  type: 'object',
  properties: {
    findings: { type: 'array', items: { type: 'object', properties: {
      title: { type: 'string' },
      severity: { type: 'string', enum: ['A', 'B', 'C'] },
      evidence: { type: 'string', description: 'probe path plus its output, or file:line plus the input that reaches it' },
      smallest_fix: { type: 'string' },
    }, required: ['title', 'severity', 'evidence', 'smallest_fix'] } },
    checked_clean: { type: 'array', items: { type: 'string' } },
    not_demonstrated: { type: 'array', items: { type: 'string' } },
  },
  required: ['findings', 'checked_clean', 'not_demonstrated'],
}

phase('Find')
const finderThunks = GROUPS.map(g => () => agent(
  `You are one of three adversarial reviewers of a DocIQ build package before it merges. ` +
  `Read the review brief ${TEMPLATE} in full, then the package section ${SECTION} in full, then every document they point you at. ` +
  `Follow the brief's rules exactly (read-only repository; no state-changing git; no -k; no pip install; probes in files, not heredocs). ` +
  `YOUR lenses are: ${g.lenses}. The other two reviewers cover the remaining lenses, so go deep on yours rather than wide. ` +
  `The corpus measurement the package section may require is done by a separate agent; do not run it. ` +
  `Write your probes and their outputs under ${OUT}\\${g.key}\\. ` +
  `Standard of evidence from the brief applies: a finding is demonstrated or it goes under not_demonstrated. Refute your own findings before reporting them. ` +
  `Return structured output only.`,
  { label: `find:${pkg}:${g.key}`, phase: 'Find', model: 'opus', schema: FIND_SCHEMA }))
if (args.measure) {
  finderThunks.push(() => agent(
    `${args.measure}\n\nWrite scripts and results under ${OUT}\\measure\\. Read-only on every repository; no state-changing git; no pip install; ` +
    `scripts in files, not heredocs. Client data rule D-12: counts only, never any text, and no corpus file names. ` +
    `Final message under 700 words: the counter validation, the before/after table, and for every changed first date and every new Bates candidate the construct that caused it (by construct kind, never text).`,
    { label: `measure:${pkg}`, phase: 'Find', model: 'sonnet' }))
}
const results = await parallel(finderThunks)
const found = results.slice(0, GROUPS.length)
const measurement = args.measure ? results[GROUPS.length] : null

const all = []
found.forEach((r, i) => { if (r) r.findings.forEach(f => all.push({ ...f, group: GROUPS[i].key })) })
const missing = found.map((r, i) => r ? null : GROUPS[i].key).filter(Boolean)
if (missing.length) log(`reviewer(s) returned nothing: ${missing.join(', ')}`)

phase('Dedupe')
const DEDUPE_SCHEMA = {
  type: 'object',
  properties: {
    groups: { type: 'array', items: { type: 'object', properties: {
      members: { type: 'array', items: { type: 'integer' }, description: 'indices into the input list that describe the same defect' },
      severity: { type: 'string', enum: ['A', 'B', 'C'], description: 'the highest member severity' },
    }, required: ['members', 'severity'] } },
  },
  required: ['groups'],
}
let distinct = all.map((f, i) => ({ ...f, merged_from: [i] }))
if (all.length > 1) {
  const d = await agent(
    `Group these review findings so each group is ONE underlying defect (same root cause and code path). Every index 0..${all.length - 1} must appear in exactly one group. ` +
    `Do not merge findings that merely touch the same file.\n\n${JSON.stringify(all.map((f, i) => ({ i, title: f.title, severity: f.severity, evidence: f.evidence.slice(0, 600) })), null, 1)}`,
    { label: `dedupe:${pkg}`, phase: 'Dedupe', model: 'sonnet', schema: DEDUPE_SCHEMA })
  if (d && d.groups.length) {
    const covered = new Set(d.groups.flatMap(g => g.members))
    const complete = all.every((_, i) => covered.has(i))
    if (complete) {
      distinct = d.groups.map(g => {
        const members = g.members.filter(i => i >= 0 && i < all.length)
        const lead = members.map(i => all[i]).sort((a, b) => a.severity.localeCompare(b.severity))[0]
        return { ...lead, severity: g.severity, merged_from: members,
                 also_reported_as: members.map(i => `[${all[i].group}] ${all[i].title}`) }
      })
    } else {
      log('dedupe did not cover every finding; verifying undeduplicated list')
    }
  }
}
const ab = distinct.filter(f => f.severity !== 'C')
const MAX_VERIFY = 9
const toVerify = ab.slice(0, MAX_VERIFY)
if (ab.length > MAX_VERIFY) log(`${ab.length - MAX_VERIFY} distinct A/B finding(s) NOT independently verified (cap ${MAX_VERIFY})`)
log(`${all.length} raw finding(s) -> ${distinct.length} distinct: ${ab.length} A/B`)

phase('Refute')
const VERDICT = {
  type: 'object',
  properties: {
    real: { type: 'boolean' },
    severity: { type: 'string', enum: ['A', 'B', 'C', 'none'] },
    reasoning: { type: 'string' },
    reproduction: { type: 'string', description: 'what you ran and what it printed' },
  },
  required: ['real', 'severity', 'reasoning', 'reproduction'],
}
const verified = await parallel(toVerify.map((f, i) => () => agent(
  `You are a skeptic. Try to REFUTE this finding from an adversarial review of a DocIQ build package. ` +
  `Context: review brief ${TEMPLATE} and package section ${SECTION} (read both; same read-only rules; your probes under ${OUT}\\verify_${i}\\). ` +
  `Reproduce the evidence yourself from scratch. It is refuted if the probe does not show what is claimed, the input cannot reach the path in the product, ` +
  `the behaviour is what the specification or a ruling in docs/decisions/decision_register.md requires, or the package section says it is out of scope. ` +
  `If it is real, set severity by the brief's scale (A blocks the merge; B fix inside this package; C record and move on). Default to real=false if you cannot reproduce it.\n\n` +
  `FINDING:\n${JSON.stringify(f, null, 2)}`,
  { label: `refute:${pkg}:${i}`, phase: 'Refute', model: 'opus', schema: VERDICT }).then(v => ({ ...f, verdict: v }))))

phase('Write')
const payload = {
  package: pkg,
  verified: verified.filter(Boolean),
  unverified_ab: ab.slice(MAX_VERIFY),
  c_findings: distinct.filter(f => f.severity === 'C'),
  measurement,
  checked_clean: found.filter(Boolean).flatMap((r, i) => r.checked_clean.map(c => `[${GROUPS[i].key}] ${c}`)),
  not_demonstrated: found.filter(Boolean).flatMap(r => r.not_demonstrated),
  reviewers_missing: missing,
}
const written = await agent(
  `Turn the JSON below into a findings report in Markdown, under 1,800 words, plain English, and RETURN THE MARKDOWN AS YOUR FINAL MESSAGE (do not write any file). ` +
  `Start with a line '# Package ${pkg}: review findings'. Sections: "Findings" (verdict.real true only, most severe first by verdict.severity; evidence, the skeptic's reproduction in one or two lines, the smallest fix; name the also_reported_as duplicates in one line); ` +
  `"Refuted" (one line each, why); "Unverified" (unverified_ab and c_findings, marked not independently verified); "Corpus measurement" (the measurement text, condensed, if present); ` +
  `"Checked and clean" (deduplicated); "Not demonstrated". Invent nothing.\n\n${JSON.stringify(payload, null, 2)}`,
  { label: `write:${pkg}`, phase: 'Write', model: 'sonnet' })
return { written, payload }
