// What /api sends (src/finanze/web/wire.py): amounts and shares are text, so as not to lose
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

export interface Series {
	currency: string;
	first: Day | null;
	total: Amount[];
	parts: { name: string; values: Amount[] }[];
	notes: string[];
}

export interface NetWorth {
	series: Series | null;
	errors: string[];
}
