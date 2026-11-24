import { everyName } from '#lib/cashflow/recurring.js';
import { day, dayOfYear } from '#lib/format/date.js';
import { money } from '#lib/format/number.js';
import type { Attention, RecurringChange } from '#lib/types.js';

/** What a line is about: its icon goes by it. */
export type NoticeKind = 'staged' | 'behind' | 'differ' | 'uncategorised' | 'up' | 'new';

/** One line of the strip: what waits, and where it is dealt with. */
export interface NoticeLine {
	key: string;
	kind: NoticeKind;
	/** `waiting` is work to do; `caution` is something off. */
	tone: 'waiting' | 'caution';
	text: string;
	action: { label: string; href: string };
}

/** Where the lines lead: the component resolves the pages. */
export interface NoticeLinks {
	importing: (owner: string) => string;
	checks: string;
	transactions: (query: Record<string, string>) => string;
}

/** Lines shown at most; beyond them all but one fold behind "N more". */
export const MOST = 5;

const counted = (count: number, one: string, many: string) =>
	count === 1 ? `1 ${one}` : `${count} ${many}`;

/** What changed about a recurring payment, as a sentence. */
export function changeText(
	payment: RecurringChange,
	currency: string,
	onePerson: boolean
): string {
	const name = `${onePerson ? '' : `${payment.owner}’s `}${payment.name}`;
	const since = dayOfYear(payment.since);
	const every = everyName(payment.every);
	const paid = payment.change === null ? null : `${money(payment.change)} ${currency}`;
	if (payment.kind === 'up') {
		return paid === null
			? `${name} costs more since ${since}.`
			: `${name} costs ${paid} more ${every} since ${since}.`;
	}
	return paid === null
		? `${name} is a new payment since ${since}.`
		: `${name} is a new payment: ${paid} ${every} since ${since}.`;
}

/** Everything that waits, in the order it is best dealt with: statements to commit, accounts
 * behind, figures that differ, rows to categorise, then recurring payments that changed. */
export function noticeLines(
	attention: Attention,
	onePerson: boolean,
	links: NoticeLinks
): NoticeLine[] {
	const whose = (owner: string) => (onePerson ? '' : ` of ${owner}`);
	const are = (count: number) => (count === 1 ? 'is' : 'are');
	const lines: NoticeLine[] = [];
	for (const waiting of attention.staged) {
		const statements = counted(waiting.count, 'statement', 'statements');
		lines.push({
			key: `staged|${waiting.owner}`,
			kind: 'staged',
			tone: 'waiting',
			text: `${statements}${whose(waiting.owner)} ${are(waiting.count)} staged and not yet counted.`,
			action: { label: 'Commit them', href: links.importing(waiting.owner) }
		});
	}
	for (const account of attention.behind) {
		const ends = account.committed ? day(account.committed) : 'no day yet';
		const name = `${onePerson ? '' : `${account.owner}’s `}${account.account}`;
		lines.push({
			key: `behind|${account.owner}|${account.account}`,
			kind: 'behind',
			tone: 'caution',
			text: `${name} statements end on ${ends}, over a month before the others.`,
			action: { label: 'Import', href: links.importing(account.owner) }
		});
	}
	if (attention.differ) {
		const differ =
			attention.differ === 1 ? '1 balance differs' : `${attention.differ} balances differ`;
		lines.push({
			key: 'differ',
			kind: 'differ',
			tone: 'caution',
			text: `${differ} from what the statements say.`,
			action: { label: 'Review them', href: links.checks }
		});
	}
	const unmatched = attention.unmatched;
	for (const waiting of unmatched === null ? [] : attention.uncategorised) {
		const rows = counted(waiting.count, 'row', 'rows');
		lines.push({
			key: `uncategorised|${waiting.owner}`,
			kind: 'uncategorised',
			tone: 'waiting',
			text: `${rows}${whose(waiting.owner)} ${are(waiting.count)} still in ${unmatched}.`,
			action: {
				label: 'Categorise',
				href: links.transactions({ category: unmatched ?? '', owner: waiting.owner })
			}
		});
	}
	for (const payment of attention.recurring) {
		lines.push({
			key: `${payment.kind}|${payment.owner}|${payment.account}|${payment.name}`,
			kind: payment.kind,
			tone: payment.kind === 'up' ? 'caution' : 'waiting',
			text: changeText(payment, attention.currency, onePerson),
			action: {
				label: 'See payments',
				href: links.transactions({
					merchant: payment.name,
					account: payment.account,
					owner: payment.owner
				})
			}
		});
	}
	return lines;
}

/** The lines shown before "N more" is asked for, and how many it holds: all of them when
 * they are `most` or fewer, else one less, so that the strip keeps to `most` lines. */
export function folded<T>(lines: readonly T[], most = MOST): { shown: T[]; more: number } {
	if (lines.length <= most) return { shown: [...lines], more: 0 };
	return { shown: lines.slice(0, most - 1), more: lines.length - most + 1 };
}
