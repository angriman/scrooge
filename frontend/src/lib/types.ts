// What /api sends (src/scrooge/web/wire.py): amounts and shares are text, so as not to lose
// a cent; days are ISO text; what is not known is null.

export type Amount = string;
export type Day = string;

export interface Person {
	name: string;
	committed: number;
	first: Day | null;
	last: Day | null;
	staged: number;
	transactions: number;
	/** Up to this day every monthly account's statements are in; null: nothing is committed. */
	covered: Day | null;
}

export interface Session {
	people: Person[];
	currencies: string[];
	currency: string;
	read_only: boolean;
	signing: boolean;
	/** Whether a category chosen can be remembered as a rule for the next import. */
	rules: boolean;
	token: string;
}

export interface Part {
	name: string;
	value: Amount;
	share: Amount | null;
	then: (Amount | null)[];
}

export interface Holding {
	symbol: string;
	name: string;
	asset: string;
	value: Amount;
	share: Amount | null;
}

export interface Change {
	days: number;
	before: Amount | null;
	amount: Amount | null;
	percent: Amount | null;
}

export interface Summary {
	currency: string;
	day: Day | null;
	through: Day | null;
	total: Amount;
	changes: Change[];
	assets: Part[];
	held: Part[];
	people: Part[];
	top: Holding[];
	rest: { count: number; value: Amount; share: Amount | null } | null;
	securities: Part;
}

export interface Check {
	owner: string;
	accounts: string;
	day: Day;
	label: string;
	currency: string;
	printed: Amount;
	derived: Amount | null;
	difference: Amount | null;
}

export interface Overview {
	overview: Summary | null;
	checks: Check[];
	logos: Record<string, string>;
	errors: string[];
}

/** What came into view on a day, by the index of the day: an account's worth on the day it
 * first counts. Days without any are left out. */
export type Arrived = Record<string, Amount>;

export interface Series {
	currency: string;
	first: Day | null;
	total: Amount[];
	arrived: Arrived;
	parts: { name: string; values: Amount[]; arrived: Arrived }[];
	notes: string[];
}

export interface NetWorth {
	series: Series | null;
	errors: string[];
}

/** An account a source feeds, and the last day committed of it. */
export interface Account {
	owner: string;
	account: string;
	label: string;
	committed: Day | null;
}

export interface MonthCategory {
	name: string;
	spent: Amount;
	usual: Amount | null;
	/** More (less, when negative) than usual, in percent of the usual month. */
	against_usual: Amount | null;
}

/** One month at a glance (src/scrooge/recap.py). Months are written `2026-10`. */
export interface MonthRecap {
	currency: string;
	month: string;
	first: Day;
	/** Its last day, or the last day there is while it is not over. */
	last: Day;
	over: boolean;
	before: Amount | null;
	after: Amount;
	moved: Amount | null;
	income: Amount;
	spent: Amount;
	/** What accounts held on the day they first counted, within the month. */
	arrived: Amount;
	saved: Amount;
	/** What moved net worth besides what was saved: markets, and what is not income or spending. */
	other: Amount | null;
	/** Saved, in percent of income: the month's, and the twelve months' ending with it. */
	rate: Amount | null;
	rate_year: Amount | null;
	/** The income from work, the salary. */
	work: Amount;
	/** Saved, in percent of salary: the month's, and the twelve months' ending with it. */
	rate_salary: Amount | null;
	rate_salary_year: Amount | null;
	categories: MonthCategory[];
	/** Monthly accounts whose statements end before `last`. */
	missing: Account[];
	earlier: string | null;
	later: string | null;
}

export interface Month {
	month: MonthRecap | null;
	errors: string[];
}

export interface Attention {
	staged: { owner: string; count: number }[];
	behind: Account[];
	/** Lines of statements whose figures differ from those worked out here. */
	differ: number;
	/** What `RecurringChange.change` is in. */
	currency: string;
	/** The category of the rows no rule matched (`unmatched` in the config); null: none. */
	unmatched: string | null;
	/** Committed rows still in `unmatched`, per person. */
	uncategorised: { owner: string; count: number }[];
	/** Recurring payments still paid that went up or started lately, the latest first; null
	 * unless asked for (`recurring=1`), as only Overview does. */
	recurring: RecurringChange[] | null;
	errors: string[];
}

/** A recurring payment that went up lately (`change`: by how much a payment) or is new
 * (`change`: what a payment costs), in `Attention.currency`; null when a rate is missing. */
export interface RecurringChange {
	/** The payment's first row's id, as `RecurringPayment.id`. */
	id: string;
	/** Who was paid, as the `merchant` filter reads them. */
	name: string;
	account: string;
	owner: string;
	every: 'monthly' | 'yearly';
	kind: 'up' | 'new';
	change: Amount | null;
	/** The first payment at the new price, or the first of all. */
	since: Day;
	/** Whether `merchant`, `account` and `owner` list its rows and no other payee's. */
	alone: boolean;
}

/** One instrument in one account (src/scrooge/portfolio.py `Position`). Native figures are in
 * the instrument's currency; `value` is in the currency asked for. */
export interface Position {
	owner: string;
	account: string;
	quantity: Amount;
	/** The close it is valued at; null for a bill. */
	price: Amount | null;
	price_day: Day | null;
	/** No close is stored: the price of a trade stands in. */
	price_is_trade: boolean;
	value_native: Amount;
	value: Amount;
	cost_native: Amount | null;
	gain_native: Amount | null;
	gain_percent: Amount | null;
}

/** One instrument, every account together, with its positions. */
export interface HeldInstrument {
	symbol: string;
	name: string;
	asset: string;
	currency: string;
	value: Amount;
	/** Of all securities, in percent. */
	share: Amount;
	cost: Amount | null;
	gain: Amount | null;
	return_percent: Amount | null;
	positions: Position[];
}

export interface SpreadSlice {
	name: string;
	value: Amount;
	share: Amount;
}

/** A bill or bond held, valued on `ladder_day`; its face is what it repays. */
export interface Rung {
	symbol: string;
	name: string;
	owner: string;
	account: string;
	currency: string;
	maturity: Day | null;
	/** From `ladder_day` to the maturity; below zero: matured. */
	days: number | null;
	face: Amount;
	value_native: Amount;
	value: Amount;
	interest: Amount | null;
}

export interface Portfolio {
	currency: string;
	day: Day | null;
	through: Day | null;
	total: Amount;
	/** Of the positions whose cost is known: `unknown_cost` are in neither. */
	cost: Amount;
	gain: Amount;
	return_percent: Amount | null;
	unknown_cost: number;
	accounts: number;
	holdings: HeldInstrument[];
	spread: Record<'asset' | 'currency' | 'account' | 'owner', SpreadSlice[]>;
	ladder_day: Day | null;
	ladder: Rung[];
	/** The cash of the pension accounts: in Pillar 3a on the overview, not listed here. */
	pension_cash: Amount | null;
	notes: string[];
}

export interface Holdings {
	holdings: Portfolio | null;
	logos: Record<string, string>;
	errors: string[];
}

/** One statement file (src/scrooge/months.py `Statement`). */
export interface StatementFile {
	id: number;
	filename: string;
	label: string;
	first: Day;
	last: Day;
	/** False: the statement prints nothing to reconcile against. */
	checked: boolean;
	transactions: number;
	committed: boolean;
	imported: Day;
	/** Where its file can be downloaded, when it is kept. */
	original: string | null;
}

/** An account a source feeds, with its statements. */
export interface StatementsAccount extends Account {
	source: string;
	/** The last day staged beyond what is committed. */
	staged: Day | null;
	behind: boolean;
	/** Whether its statements run month by month to their end. */
	monthly: boolean;
	/** What it holds now, cash and securities, in the currency asked for. */
	balance: Amount | null;
	spans: { first: Day; last: Day; committed: boolean }[];
	statements: StatementFile[];
	/** What it holds by part; null when its balance is not known. */
	holds: AccountHolds | null;
	/** The accounts it is booked as elsewhere: a broker's one per currency, whose statements
	 * it lists together; its own name alone otherwise. */
	parts: string[];
}

/** What an account holds now by part, and its balance at the last month-ends. */
export interface AccountHolds {
	/** Its cash in each currency, the largest first: as kept, and in the currency asked for.
	 * `asset` is the kind net worth counts it as: `Cash`, `Card` (what a card owes), `Pillar 3a`
	 * (the cash inside one) or `Pillar 2` (what a pension fund's certificates state). */
	cash: { asset: string; currency: string; amount: Amount; converted: Amount }[];
	/** Its securities together, in the currency asked for; null when it holds none. */
	securities: Amount | null;
	/** Oldest first, at most twelve; the last is the last day there is. */
	months: { day: Day; balance: Amount }[];
}

export interface Statements {
	currency: string;
	/** Up to this day every monthly account's statements are in. */
	covered: Day | null;
	accounts: StatementsAccount[];
	checks: Check[];
	errors: string[];
}

/** A heading of the taxonomy and the categories under it. */
export interface Headed {
	heading: string;
	names: string[];
}

export interface Transaction {
	id: string;
	owner: string;
	account: string;
	date: string;
	booking_date: string;
	amount: Amount;
	currency: string;
	description: string;
	counterparty: string;
	kind: string;
	category: string | null;
	category_parent: string | null;
	file: string;
	/** How often it comes back, when it does. */
	recurring: 'monthly' | 'yearly' | null;
	/** Whether its category can be changed here. */
	changeable: boolean;
}

export interface Total {
	currency: string;
	count: number;
	total: Amount;
}

/** What the rows of one top-level category add up to, in one currency. */
export interface CategoryTotal extends Total {
	name: string;
}

export interface Transactions {
	/** How many rows match, of which `rows` are those from `offset` on. */
	count: number;
	offset: number;
	totals: Total[];
	by_category: CategoryTotal[];
	rows: Transaction[];
	choices: {
		accounts: string[];
		/** What the category filter can be set to: headings and the names under them. */
		categories: Headed[];
		/** What a row can be given. */
		leaves: Headed[];
	};
	kinds: string[];
	errors: string[];
}

/** A row of a staged statement, with the category it has, if any. */
export interface StagedRow {
	id: string;
	date: string;
	account: string;
	amount: Amount;
	currency: string;
	counterparty: string;
	description: string;
	category: string | null;
	side: string | null;
	/** Whether a category can be chosen for it here: it has none, or one a rule gave. */
	choosable: boolean;
	statement: number | null;
}

/** What is staged for one person. */
export interface Staged {
	/** Who statements can be imported for, and whose these are. */
	owners: string[];
	owner: string;
	files: {
		id: number;
		label: string;
		filename: string;
		first: string;
		last: string;
		checked: boolean;
		transactions: number;
	}[];
	rows: StagedRow[];
	/** The rows waiting for a category, by counterparty and direction. */
	groups: { name: string; incoming: boolean; ids: string[] }[];
	totals: {
		side: string | null;
		category: string | null;
		currency: string;
		count: number;
		total: Amount;
	}[];
	/** What only another statement can settle: it cannot be committed before. */
	gaps: string[];
	/** How many rows wait for a category: none may, to commit. */
	waiting: number;
	choices: Transactions['choices'];
	/** What a new expense category can go under. */
	under: string[];
	rules: boolean;
	/** Whether bills without a maturity can be looked up. */
	lookup: boolean;
	errors: string[];
}

/** What became of the files of an import. */
export interface Imported {
	files: {
		name: string;
		label: string;
		problem: string | null;
		staged: boolean;
		nothing_new: boolean;
	}[];
	certificates: {
		account: string;
		filename: string;
		balance: Amount;
		currency: string;
		as_of: string;
		adds_up: boolean;
	}[];
	unreconciled: string[];
	already: number;
	differences: string[];
	later: number;
}

/** What an upload, or a look-up of its bills, answered. */
export interface Upload {
	imported?: Imported;
	/** Bills it stopped on, and the key that imports the same files once they are looked up. */
	undated?: { bills: string[]; held: string | null };
	notice?: string | null;
	errors: string[];
}

/** Income, spending and what was saved over a span (src/scrooge/savings.py `Totals`). */
export interface Saved {
	income: Amount;
	spent: Amount;
	saved: Amount;
	/** What was saved in percent of income; null without income. */
	rate: Amount | null;
	/** The share of income from work, in percent. */
	from_work: Amount | null;
	/** The income from work, the salary. */
	work: Amount;
	/** What was saved in percent of salary; null without salary. */
	rate_salary: Amount | null;
}

export interface CashFlowPeriod extends Saved {
	/** `2026-09`, `2026-Q3`, `2026`, as the API names it. */
	name: string;
	first: string;
	last: string;
	/** The savings rate of the twelve months ending with it. */
	rate_12: Amount | null;
	/** The same, on the salary of those twelve months. */
	rate_salary_12: Amount | null;
	/** Spent in each of `CashFlowReport.parts`. */
	spent_by: Record<string, Amount>;
	/** Earned in each income group (Lavoro, Investimenti, Altro). */
	earned_by: Record<string, Amount>;
}

export interface CashFlowReport {
	currency: string;
	totals: Saved | null;
	/** The same range a year earlier, when it is known. */
	year_before: Saved | null;
	periods: CashFlowPeriod[];
	/** What the bars stack: the largest categories, then "Others" and "Paid back". */
	parts: string[];
	/** The calendar months the range touches. */
	months: number;
	/** What the other person paid back, taken off what was spent. */
	paid_back: Amount;
	categories: {
		name: string;
		count: number;
		spent: Amount;
		share: Amount | null;
		monthly: Amount;
		/** The median month of the twelve before the range. */
		usual: Amount | null;
		year_before: Amount | null;
		leaves: { name: string; count: number; spent: Amount }[];
	}[];
	merchants: {
		name: string;
		category: string;
		count: number;
		spent: Amount;
		first: string;
		last: string;
	}[];
	other_merchants: { count: number; spent: Amount };
	sources: { group: string; name: string; count: number; total: Amount }[];
	/** Income and expenses in each currency, as paid: nothing converted. */
	as_paid: {
		period: string;
		currency: string;
		income: Amount;
		expenses: Amount;
		investing: Amount;
	}[];
	as_paid_totals: {
		currency: string;
		side: string;
		category: string;
		count: number;
		total: Amount;
	}[];
	through: string | null;
	/** Who paid what the household spent; null for one person, or with only one. */
	paid_by: PaidBy | null;
}

/** Who paid what the household spent over a range (src/scrooge/paidby.py `PaidBy`). */
export interface PaidBy {
	spent: Amount;
	/** Everyone with statements committed, by name; `share` in percent of `spent`. `settled`:
	 * what they spent, and sent the others, less what the others sent them. */
	people: {
		name: string;
		spent: Amount;
		share: Amount | null;
		settled: Amount;
		settled_share: Amount | null;
	}[];
	/** Each top-level category, the largest first, and what each person spent on it. */
	categories: { name: string; spent: Amount; by: Record<string, Amount> }[];
	/** The net of what two people paid each other over the range, `amount` above zero. */
	moves: { sender: string; receiver: string; amount: Amount }[];
}

export interface CashFlow {
	report: CashFlowReport | null;
	/** The range shown: `last` null means up to the last day committed. */
	first: string | null;
	last: string | null;
	by: 'week' | 'month' | 'quarter' | 'year';
	/** The ranges offered, as `from` and `to` take them. */
	ranges: { label: string; from: string; to: string }[];
	errors: string[];
}

/** What was paid, and withheld, over a range or in a month (src/scrooge/payouts.py `Paid`).
 * Tax withheld is negative, as stored. */
export interface Payout {
	dividends: Amount;
	interest: Amount;
	tax: Amount;
}

/** What one holding, or one account's rows that name none, paid (payouts.py `Holding`). */
export interface PayingHolding extends Payout {
	/** The counterparty the rows are grouped by, or the account's name. */
	key: string;
	name: string;
	/** As the instruments (and the logos) have it; null when no instrument has it. */
	symbol: string | null;
	/** The account, for rows that name nothing; else null. */
	account: string | null;
	/** Whose the account is, when two people have an account of its name and it is named
	 * after them; else null. */
	owner: string | null;
	count: number;
	last: Day;
}

/** What investments paid over a range (wire.py `payouts_report`). */
export interface PayoutsReport {
	currency: string;
	/** The range shown; null when nothing is committed. */
	first: Day | null;
	last: Day | null;
	through: Day | null;
	totals: Payout & { net: Amount };
	/** The same months a year earlier; null when they are not all known. */
	year_before: (Payout & { net: Amount }) | null;
	/** The twelve months up to `through`, whatever the range. */
	trailing: Payout & { net: Amount };
	months: (Payout & { month: string })[];
	holdings: PayingHolding[];
}

/** `/api/income`: what investments paid, and the ranges offered. `errors` names each currency
 * whose rows were left out for want of a rate. */
export interface InvestmentIncome {
	income: PayoutsReport;
	logos: Record<string, string>;
	ranges: { label: string; from: string; to: string }[];
	errors: string[];
}

/** What investment accounts did over a range (src/scrooge/performance.py `Measured`). */
export interface Measured {
	/** Worth at the end of the day before the range. */
	start: Amount;
	end: Amount;
	/** Money put in, less what was taken out. */
	net_flows: Amount;
	/** The change that is not money put in or taken out. */
	gain: Amount;
	/** Time-weighted, in percent over the range; null when nothing was invested. */
	twr: Amount | null;
	/** Money-weighted, in percent; null when there is no such rate. */
	mwr: Amount | null;
	/** `mwr` is a rate a year (a range of a year or more); else it is over the range. */
	mwr_a_year: boolean;
	/** The first day `twr` counts, when it is after the range's first; else null. */
	twr_since: Day | null;
}

/** One broker's accounts of one person, in every currency together. */
export interface MeasuredAccount extends Measured {
	owner: string;
	name: string;
	accounts: string[];
}

/** One day of the chart: what the accounts were worth, and the start value with what had been
 * put in since. Daily, or weekly over a long range; the first is the day before the range. */
export interface InvestedDay {
	day: Day;
	value: Amount;
	invested: Amount;
}

export interface PerformanceReport {
	currency: string;
	range: string;
	/** The range's first and last day; null when there is no investment account. */
	first: Day | null;
	last: Day | null;
	totals: Measured | null;
	series: InvestedDay[];
	accounts: MeasuredAccount[];
}

/** `/api/performance`. `errors` names each currency whose accounts were left out for want of a
 * rate, and those accounts. */
export interface InvestmentPerformance {
	performance: PerformanceReport;
	errors: string[];
}

/** Net worth at a month's end in one currency, and how it moved since the month before, less
 * what accounts brought when they came into view (src/scrooge/networth.py `Figure`). */
export interface MonthEndFigure {
	total: Amount;
	/** Null for the first month: nothing before it. */
	change: Amount | null;
	/** In percent of the month before; null unless that was above zero. */
	percent: Amount | null;
	/** What of `total` can be reached, and what is frozen: the pensions and the accounts the
	 * config names `frozen`. */
	liquid: Amount;
	frozen: Amount;
	/** What accounts held when they were first counted within the month, left out of `change`:
	 * the two add up to the difference of the totals. Null with `change`. */
	arrived: Amount | null;
}

/** The last day of one month there is, and net worth on it by currency. */
export interface MonthEnd {
	/** `2026-10`. */
	month: string;
	day: Day;
	/** The month is not over: `day` is the last day there is. */
	partial: boolean;
	values: Record<string, MonthEndFigure>;
}

export interface NetWorthMonths {
	/** The table's currencies, in the order of the config. */
	currencies: string[];
	/** Newest first. */
	months: MonthEnd[];
	errors: string[];
}

/** A payment that comes back at a steady pace, as paid (positive). */
export interface RecurringPayment {
	/** Its first payment's id: two can have the same name, never the same id. */
	id: string;
	/** Who was paid, as the cash flow names them and the `merchant` filter reads them. */
	name: string;
	owner: string;
	account: string;
	every: 'monthly' | 'yearly';
	amount: Amount;
	currency: string;
	/** What the payment before the latest was, when the price changed between the two. */
	previous: Amount | null;
	count: number;
	first: string;
	last: string;
	next: string;
	/** The latest amount over a month, in `Recurring.currency`; null when a rate is missing. */
	monthly: Amount | null;
	/** Whether the `merchant` filter, with `account` and `owner`, lists its rows and no other
	 * payee's: the line opens them only then. */
	alone: boolean;
}

export interface Recurring {
	currency: string;
	/** The last day of the statements, the day the list is as of. */
	through: string | null;
	/** What the active ones cost a month; null when a rate is missing. */
	monthly: Amount | null;
	active: RecurringPayment[];
	ended: RecurringPayment[];
	errors: string[];
}

/** One category, merchant or account over the last two years (`/api/transactions/context`). */
export interface TransactionsContext {
	kind: 'category' | 'merchant' | 'account';
	name: string;
	currency: string;
	/** The amounts are money earned (a category of income), not money spent. */
	earned: boolean;
	/** Two years of months up to the one of `through`, oldest first; none when nothing is
	 * committed or a rate is missing. */
	months: { month: string; amount: Amount }[];
	/** The median of the twelve months before the last; null when they are not all known. */
	usual: Amount | null;
	this_month: Amount | null;
	through: Day | null;
	/** The last month is not over in the statements committed. */
	partial: boolean;
	/** For a category: where most was spent (or earned from) over those months. */
	merchants: { name: string; amount: Amount; count: number }[] | null;
	/** For an account: what it holds now. */
	balance: Amount | null;
	errors: string[];
}

/** A file of rules: a person's own, tried first, or one everybody shares. */
export interface RulesFile {
	key: string;
	owner: string | null;
}

/** One rule of `/api/settings/rules`: where it is, and how many committed rows it matches. */
export interface Rule {
	file: string;
	owner: string | null;
	/** From 1, in its file. */
	position: number;
	match: string;
	category: string;
	/** `in`, `out`, or empty for both. */
	direction: string;
	account: string;
	/** Committed rows it would match on its own, whatever rule before it takes them first;
	 * null when its pattern is none. */
	matches: number | null;
	/** What a change of it sends back: its lines as shown. */
	version: string;
	/** Why it cannot be changed here; null when it can. */
	fixed: string | null;
}

export interface SettingsRules {
	people: string[];
	files: RulesFile[];
	rules: Rule[];
	/** The categories a rule can give. */
	leaves: Headed[];
	/** Whether this app can change them: not on a copy, nor without a place for rules. */
	editable: boolean;
	errors: string[];
}

/** What a pattern tried on the committed rows matches; nothing is saved. */
export interface PatternTest {
	count: number;
	/** The newest of them. */
	rows: Transaction[];
	errors: string[];
}

export interface ListedCategory {
	name: string;
	side: 'uscite' | 'entrate' | 'transfer';
	/** The top-level category of an expense. */
	parent: string | null;
	built_in: boolean;
	/** Committed rows that carry it. */
	rows: number;
}

export interface SettingsCategories {
	categories: ListedCategory[];
	/** What a new expense category can go under. */
	groups: string[];
	/** What the config gives rows no rule matches: it is not renamed, merged away or removed. */
	unmatched: string | null;
	editable: boolean;
	errors: string[];
}
