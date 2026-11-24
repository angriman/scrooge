// The rules on the settings page: by the file they are kept in, in the order they are tried,
// and what a change of one sends.

import type { Rule, RulesFile } from '#lib/types.js';

/** What a rule can be changed to there. */
export interface Draft {
	match: string;
	category: string;
	direction: string;
}

export interface FileRules {
	file: RulesFile;
	rules: Rule[];
}

/** Each file with its rules, in the order an import tries them; a file without rules too. */
export function byFile(files: readonly RulesFile[], rules: readonly Rule[]): FileRules[] {
	return files.map((file) => ({ file, rules: rules.filter((rule) => rule.file === file.key) }));
}

/** What a file is, for people. */
export function fileTitle(file: RulesFile): string {
	return file.owner ? `${file.owner}'s own rules` : 'Shared rules';
}

/** What a file is for, under its title. */
export function fileNote(file: RulesFile): string {
	return file.owner
		? `${file.key}: for rows of ${file.owner}'s accounts, tried before the shared rules.`
		: `${file.key}: for everybody's rows.`;
}

/** Why rules stay in their file. */
export const STAY =
	"A rule moves up or down within its file only: to make a person's rule a shared one, or the other way round, change the files themselves.";

/** A rule's key among all: its file and place. */
export const ruleKey = (rule: Rule): string => `${rule.file}#${rule.position}`;

/** What it says about money in or out. */
export function directionName(direction: string): string {
	if (direction === 'in') return 'money in';
	if (direction === 'out') return 'money out';
	return 'in and out';
}

/** How many committed rows it would match on its own, in words: a rule before it may take
 * some of them first. */
export function matchesName(matches: number | null): string {
	if (matches === null) return 'not a pattern';
	if (matches === 0) return 'would match no rows';
	return `would match ${matches === 1 ? '1 row' : `${matches} rows`} on its own`;
}

/** A draft of `rule` as it is. */
export const draftOf = (rule: Rule): Draft => ({
	match: rule.match,
	category: rule.category,
	direction: rule.direction
});

/** Whether `draft` changes `rule`. */
export function changes(rule: Rule, draft: Draft): boolean {
	return (
		draft.match !== rule.match ||
		draft.category !== rule.category ||
		draft.direction !== rule.direction
	);
}

/** Which rule a change is about, as it was shown: a rule changed since is refused. */
export function seen(rule: Rule) {
	return { file: rule.file, position: rule.position, version: rule.version };
}

/** What `/api/settings/rules/edit` takes. */
export const edited = (rule: Rule, draft: Draft) => ({ ...seen(rule), ...draft });

/** The query of `/api/settings/rules/test` for a pattern. */
export function testQuery(match: string, direction: string, owner: string): URLSearchParams {
	const query = new URLSearchParams({ match });
	if (direction) query.set('direction', direction);
	if (owner) query.set('owner', owner);
	return query;
}
