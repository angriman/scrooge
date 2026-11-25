export const en = {
	'nav.pages': 'Pages',
	'nav.overview': 'Overview',
	'nav.cashflow': 'Cash flow',
	'nav.investments': 'Investments',
	'nav.accounts': 'Accounts',
	'nav.transactions': 'Transactions',
	'nav.household': 'Household',
	'nav.whoseFigures': 'Whose figures',
	'nav.currency': 'Currency',
	'nav.language': 'Language',
	'nav.settingsLabel': 'Settings: categories and rules',
	'nav.settingsTitle': 'Categories and rules',
	'nav.signOut': 'Sign out',
	'nav.importLabel': 'Import statements',
	'nav.import': 'Import',
	'nav.completeUpTo': 'Complete up to {date}',
	'nav.upTo': 'Up to {date}',
	'nav.statementsCompleteLabel': 'Statements complete up to {date}',
	'nav.statementsCompleteTitle': "Every monthly account's statements are in up to this day"
} as const;

export type MessageKey = keyof typeof en;
export type Messages = Record<MessageKey, string>;
