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
}

export interface Session {
	people: Person[];
	currencies: string[];
	currency: string;
	read_only: boolean;
	signing: boolean;
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
	errors: string[];
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
}

export interface Statements {
	currency: string;
	/** Up to this day every monthly account's statements are in. */
	covered: Day | null;
	accounts: StatementsAccount[];
	checks: Check[];
	errors: string[];
}
