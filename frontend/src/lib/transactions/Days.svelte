<!--
@component
The transactions day by day, newest first: each day with what it adds up to in each of its
currencies, and its rows under it.
-->
<script lang="ts">
	import { weekday } from '#lib/format/date.js';
	import { signed, sum } from '#lib/format/number.js';
	import type { Headed, Transaction } from '#lib/types.js';
	import TransactionRow from './TransactionRow.svelte';

	interface Props {
		rows: Transaction[];
		/** Whether the rows are counted by their booking date rather than the day they happened. */
		booking: boolean;
		leaves: Headed[];
		people: boolean;
		rules: boolean;
		changeable: boolean;
		thisYear: number;
		onchange: (row: Transaction, category: string, remember: boolean) => Promise<void>;
	}

	let { rows, booking, leaves, people, rules, changeable, thisYear, onchange }: Props = $props();

	const days = $derived.by(() => {
		const found: { day: string; rows: Transaction[] }[] = [];
		for (const row of rows) {
			const day = booking ? row.booking_date : row.date;
			const last = found.at(-1);
			if (last?.day === day) last.rows.push(row);
			else found.push({ day, rows: [row] });
		}
		return found.map((group) => {
			const currencies = [...new Set(group.rows.map((row) => row.currency))];
			const totals = currencies.map((currency) => ({
				currency,
				total: sum(
					group.rows.filter((row) => row.currency === currency).map((row) => row.amount)
				)
			}));
			return { ...group, totals };
		});
	});
</script>

<div class="space-y-4">
	{#each days as group (group.day)}
		<section aria-label={weekday(group.day, thisYear)}>
			<header class="flex items-baseline justify-between gap-4 px-4 pb-1.5">
				<h3 class="text-small font-medium text-ink-secondary">
					{weekday(group.day, thisYear)}
				</h3>
				<p class="text-caption text-ink-muted tabular-nums">
					{#each group.totals as total, index (total.currency)}{index ? ', ' : ''}{signed(
							total.total
						)}
						{total.currency}{/each}
				</p>
			</header>
			<ul class="rounded-panel bg-panel">
				{#each group.rows as row (row.id)}
					<TransactionRow {row} {leaves} {people} {rules} {changeable} {onchange} />
				{/each}
			</ul>
		</section>
	{/each}
</div>
