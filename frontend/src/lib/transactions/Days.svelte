<!--
@component
The transactions day by day, newest first: a shared desktop column header bar, then each day's
header with its exact per-currency totals and its compact aligned rows beneath.
-->
<script lang="ts">
	import { weekday } from '#lib/format/date.js';
	import { signed } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Transaction } from '#lib/types.js';
	import { cn } from '#lib/ui/cn.js';
	import TransactionRow from './TransactionRow.svelte';
	import type { DetailKind } from './detail.js';
	import { groupByDay, ROW_GRID } from './summary.js';

	interface Props {
		rows: Transaction[];
		/** Whether the rows are counted by their booking date rather than the day they happened. */
		booking: boolean;
		people: boolean;
		changeable: boolean;
		thisYear: number;
		activeDetail: string | null;
		ondetail: (kind: DetailKind, name: string, trigger: HTMLElement) => void;
		onedit: (row: Transaction, trigger: HTMLElement) => void;
	}

	let { rows, booking, people, changeable, thisYear, activeDetail, ondetail, onedit }: Props =
		$props();

	const days = $derived(groupByDay(rows, booking));
</script>

<div class="space-y-3">
	<div
		aria-hidden="true"
		class={cn(
			'hidden items-center gap-x-3 border-b border-line px-3 pb-1.5 text-caption font-medium text-ink-muted md:grid',
			ROW_GRID
		)}
	>
		<span>{t('transactions.colDate')}</span>
		<span>{t('transactions.colDescription')}</span>
		<span>{t('transactions.colCategory')}</span>
		<span>{t('transactions.colAccount')}</span>
		<span class="text-right">{t('transactions.colAmount')}</span>
	</div>

	{#each days as group (group.day)}
		<section aria-label={weekday(group.day, thisYear)}>
			<header class="flex items-baseline justify-between gap-4 px-3 pb-1">
				<h3 class="text-small font-medium text-ink-secondary">{weekday(group.day, thisYear)}</h3>
				<p class="text-right text-caption text-ink-muted tabular-nums">
					{#each group.totals as total, index (total.currency)}{#if index}, {' '}{/if}<span
							class="whitespace-nowrap"
							>{signed(total.total)}<span class="ml-1.5 inline-block w-[3.2ch] text-left"
								>{total.currency}</span
							></span
						>{/each}
				</p>
			</header>
			<ul class="rounded-panel bg-panel">
				{#each group.rows as row (row.id)}
					<TransactionRow
						{row}
						{booking}
						{thisYear}
						{people}
						{changeable}
						{activeDetail}
						{ondetail}
						{onedit}
					/>
				{/each}
			</ul>
		</section>
	{/each}
</div>
