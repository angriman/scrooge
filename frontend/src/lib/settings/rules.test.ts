import { describe, expect, it } from 'vitest';
import { setLanguage } from '#lib/i18n/index.svelte.js';
import type { Rule, RulesFile } from '#lib/types.js';
import {
	byFile,
	changes,
	directionName,
	draftOf,
	edited,
	fileNote,
	fileTitle,
	matchesName,
	moveTargets,
	moved,
	movedTo,
	ruleKey,
	testQuery
} from './rules.js';

const own: RulesFile = { key: 'rules.lukas.yaml', owner: 'Lukas' };
const anna: RulesFile = { key: 'rules.anna.yaml', owner: 'Anna' };
const shared: RulesFile = { key: 'rules.yaml', owner: null };
const extraShared: RulesFile = { key: 'more/rules.yaml', owner: null };

const rule = (file: RulesFile, position: number, match = 'KONSUM'): Rule => ({
	file: file.key,
	owner: file.owner,
	position,
	match,
	category: 'Groceries',
	direction: '',
	account: '',
	matches: 2,
	version: `v${position}`,
	fixed: null,
	counted: true
});

describe('byFile', () => {
	it('keeps the order of the files, and a file without rules', () => {
		const rules = [rule(shared, 1), rule(shared, 2, 'ACME')];
		expect(byFile([own, shared], rules)).toEqual([
			{ file: own, rules: [] },
			{ file: shared, rules }
		]);
	});
});

describe('names', () => {
	it('says whose a file is without showing internal YAML filenames', () => {
		expect(fileTitle(own)).toBe("Lukas's own rules");
		expect(fileTitle(shared)).toBe('Shared rules');
		expect(fileNote(own)).toBe("For rows of Lukas's accounts, tried before the shared rules.");
		expect(fileNote(own)).not.toContain('rules.lukas.yaml');
		expect(fileNote(shared)).toBe("For everybody's rows.");
		expect(fileNote(shared)).not.toContain('rules.yaml');
	});

	it('says the direction and the matches in words', () => {
		expect(directionName('in')).toBe('money in');
		expect(directionName('out')).toBe('money out');
		expect(directionName('')).toBe('in and out');
		expect(matchesName(null)).toBe('not a pattern');
		expect(matchesName(null, false)).toBe('not counted: counting took too long');
		expect(matchesName(0)).toBe('would match no rows');
		expect(matchesName(1)).toBe('would match 1 row on its own');
		expect(matchesName(12)).toBe('would match 12 rows on its own');
	});

	it('keys a rule by its file and place', () => {
		expect(ruleKey(rule(own, 3))).toBe('rules.lukas.yaml#3');
	});

	it('translates rule file and match labels into Italian', () => {
		setLanguage('it');
		try {
			expect(fileTitle(own)).toBe('Regole personali di Lukas');
			expect(fileTitle(shared)).toBe('Regole condivise');
			expect(fileNote(own)).toBe('Per i conti di Lukas, valutate prima delle regole condivise.');
			expect(fileNote(own)).not.toContain('rules.lukas.yaml');
			expect(fileNote(shared)).toBe('Per i movimenti di tutti.');
			expect(fileNote(shared)).not.toContain('rules.yaml');
			expect(directionName('in')).toBe('in entrata');
			expect(matchesName(1)).toBe('abbinerebbe 1 movimento');
			expect(matchesName(5)).toBe('abbinerebbe 5 movimenti');
		} finally {
			setLanguage('en');
		}
	});
});

describe('a change', () => {
	it('is only one when something differs', () => {
		const shown = rule(shared, 1);
		expect(changes(shown, draftOf(shown))).toBe(false);
		expect(changes(shown, { ...draftOf(shown), direction: 'out' })).toBe(true);
	});

	it('sends the rule as it was shown, and what it becomes', () => {
		const draft = { match: 'KONSUM|COOP', category: 'Groceries', direction: 'out' as const };
		expect(edited(rule(shared, 2), draft)).toEqual({
			file: 'rules.yaml',
			position: 2,
			version: 'v2',
			...draft
		});
	});
});

describe('moved', () => {
	it('sends the rule passed as it was shown', () => {
		expect(moved(rule(shared, 2), -1, rule(shared, 1))).toEqual({
			file: 'rules.yaml',
			position: 2,
			version: 'v2',
			by: -1,
			beside: 'v1'
		});
		expect(moved(rule(shared, 2), 1, undefined).beside).toBe('');
	});
});

describe('moveTargets and movedTo', () => {
	it('picks valid destination files for single-person and owner-filtered lists', () => {
		const files = [own, shared];
		expect(moveTargets(rule(shared, 1), files)).toEqual([own]);
		expect(moveTargets(rule(own, 1), files)).toEqual([shared]);
	});

	it('picks personal targets for shared rules and shared plus other personal files for own rules', () => {
		const files = [own, anna, shared];
		expect(moveTargets(rule(shared, 1), files)).toEqual([own, anna]);
		expect(moveTargets(rule(own, 1), files)).toEqual([shared, anna]);
		expect(moveTargets(rule(anna, 1), files)).toEqual([shared, own]);
	});

	it('deduplicates multiple shared files to the primary shared file', () => {
		const files = [own, anna, shared, extraShared];
		expect(moveTargets(rule(shared, 1), files)).toEqual([own, anna]);
		expect(moveTargets(rule(extraShared, 1), files)).toEqual([own, anna]);
		expect(moveTargets(rule(own, 1), files)).toEqual([shared, anna]);
	});

	it('returns no targets when there are no people configured', () => {
		expect(moveTargets(rule(shared, 1), [shared])).toEqual([]);
	});

	it('builds the /api/settings/rules/file payload', () => {
		expect(movedTo(rule(shared, 2), 'rules.lukas.yaml')).toEqual({
			file: 'rules.yaml',
			position: 2,
			version: 'v2',
			to: 'rules.lukas.yaml'
		});
	});
});

describe('testQuery', () => {
	it('leaves out what is not set', () => {
		expect(testQuery('acme', '', '').toString()).toBe('match=acme');
		expect(testQuery('a b', 'in', 'Anna').toString()).toBe('match=a+b&direction=in&owner=Anna');
	});
});
