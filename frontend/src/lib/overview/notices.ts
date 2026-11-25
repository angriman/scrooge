import { everyName } from '#lib/cashflow/recurring.js';
import { day, dayOfYear } from '#lib/format/date.js';
import { money } from '#lib/format/number.js';
import { t } from '#lib/i18n/index.svelte.js';
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
	/** Where it is dealt with; none for a payment whose rows no filter lists alone. */
	action?: { label: string; href: string };
}

export interface NoticeOptions {
	/** Whether the figures are of one person: their name then goes without saying. */
	onePerson: boolean;
	/** Whether this is a copy, where nothing can be changed. */
	readOnly: boolean;
}

/** Where the lines lead: the component resolves the pages. */
export interface NoticeLinks {
	importing: (owner: string) => string;
	checks: string;
	transactions: (query: Record<string, string>) => string;
}

/** Lines shown at most; beyond them all but one fold behind "N more". */
export const MOST = 5;

/** What changed about a recurring payment, as a sentence. */
export function changeText(payment: RecurringChange, currency: string, onePerson: boolean): string {
	const name = onePerson
		? payment.name
		: t('notices.ownerPossessive', { owner: payment.owner, item: payment.name });
	const since = dayOfYear(payment.since);
	const every = everyName(payment.every);
	const amount = payment.change === null ? null : `${money(payment.change)} ${currency}`;
	if (payment.kind === 'up') {
		return amount === null
			? t('notices.upUnknown', { name, since })
			: t('notices.upPaid', { name, amount, every, since });
	}
	return amount === null
		? t('notices.newUnknown', { name, since })
		: t('notices.newPaid', { name, amount, every, since });
}

/** Everything that waits, in the order it is best dealt with: statements to commit, accounts
 * behind, figures that differ, rows to categorise, then recurring payments that changed. */
export function noticeLines(
	attention: Attention,
	options: NoticeOptions,
	links: NoticeLinks
): NoticeLine[] {
	const onePerson = options.onePerson;
	const lines: NoticeLine[] = [];
	for (const waiting of attention.staged) {
		const text = onePerson
			? t(waiting.count === 1 ? 'notices.stagedOne' : 'notices.stagedMany', {
					count: waiting.count
				})
			: t(waiting.count === 1 ? 'notices.stagedOwnerOne' : 'notices.stagedOwnerMany', {
					count: waiting.count,
					owner: waiting.owner
				});
		lines.push({
			key: `staged|${waiting.owner}`,
			kind: 'staged',
			tone: 'waiting',
			text,
			action: { label: t('notices.commitThem'), href: links.importing(waiting.owner) }
		});
	}
	for (const account of attention.behind) {
		const date = account.committed ? day(account.committed) : t('notices.noDayYet');
		const text = onePerson
			? t('notices.behind', { account: account.account, date })
			: t('notices.behindOwner', { owner: account.owner, account: account.account, date });
		lines.push({
			key: `behind|${account.owner}|${account.account}`,
			kind: 'behind',
			tone: 'caution',
			text,
			action: { label: t('notices.import'), href: links.importing(account.owner) }
		});
	}
	if (attention.differ) {
		lines.push({
			key: 'differ',
			kind: 'differ',
			tone: 'caution',
			text: t(attention.differ === 1 ? 'notices.differOne' : 'notices.differMany', {
				count: attention.differ
			}),
			action: { label: t('notices.reviewThem'), href: links.checks }
		});
	}
	const unmatched = attention.unmatched;
	if (unmatched !== null) {
		for (const waiting of attention.uncategorised) {
			const text = onePerson
				? t(waiting.count === 1 ? 'notices.uncategorisedOne' : 'notices.uncategorisedMany', {
						count: waiting.count,
						category: unmatched
					})
				: t(
						waiting.count === 1
							? 'notices.uncategorisedOwnerOne'
							: 'notices.uncategorisedOwnerMany',
						{
							count: waiting.count,
							owner: waiting.owner,
							category: unmatched
						}
					);
			lines.push({
				key: `uncategorised|${waiting.owner}`,
				kind: 'uncategorised',
				tone: 'waiting',
				text,
				action: {
					// A copy only shows the rows: their category is changed where the database is.
					label: t(options.readOnly ? 'notices.seeThem' : 'notices.categorise'),
					href: links.transactions({ category: unmatched, owner: waiting.owner })
				}
			});
		}
	}
	for (const payment of attention.recurring ?? []) {
		lines.push({
			key: `recurring|${payment.id}`,
			kind: payment.kind,
			tone: payment.kind === 'up' ? 'caution' : 'waiting',
			text: changeText(payment, attention.currency, onePerson),
			// A name other payees of the account share would list their rows too: no link then.
			action: payment.alone
				? {
						label: t('notices.seePayments'),
						href: links.transactions({
							merchant: payment.name,
							account: payment.account,
							owner: payment.owner
						})
					}
				: undefined
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
