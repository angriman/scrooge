<!--
@component
One transaction row: fixed-width columns on desktop (date · description · category · account ·
amount) and a compact two-line layout on mobile. Clicking a merchant or category opens its
details in the side panel; changing a row's category opens a modal dialog.
-->
<script lang="ts">
	import FileText from '@lucide/svelte/icons/file-text';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Repeat from '@lucide/svelte/icons/repeat';
	import { day, dayOfYear } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Transaction } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { formatDetail, type DetailKind } from './detail.js';
	import { kindName } from './filter.js';
	import { ROW_GRID } from './summary.js';

	interface Props {
		row: Transaction;
		/** Whether the list is ordered by booking date rather than occurrence date. */
		booking: boolean;
		thisYear: number;
		/** Whether to say whose the account is: the household is shown. */
		people: boolean;
		/** Whether anything can be changed here: not over a copy. */
		changeable: boolean;
		/** Currently open detail param (`category:...` or `merchant:...`), if any. */
		activeDetail: string | null;
		ondetail: (kind: DetailKind, name: string, trigger: HTMLElement) => void;
		onedit: (row: Transaction, trigger: HTMLElement) => void;
	}

	let { row, booking, thisYear, people, changeable, activeDetail, ondetail, onedit }: Props =
		$props();

	const who = $derived(row.counterparty || row.description);
	const what = $derived(
		row.counterparty && row.description !== row.counterparty ? row.description : ''
	);
	const rowDate = $derived(booking ? row.booking_date : row.date);
	const dateLabel = $derived(
		Number(rowDate.slice(0, 4)) === thisYear ? dayOfYear(rowDate) : day(rowDate)
	);
	const tone = $derived(
		row.kind === 'internal' || row.kind === 'transfer'
			? 'text-ink-secondary'
			: row.amount.startsWith('-')
				? 'text-ink'
				: 'text-gain'
	);

	const merchantActive = $derived(
		Boolean(row.merchant && activeDetail === formatDetail('merchant', row.merchant))
	);
	const categoryActive = $derived(
		Boolean(
			row.category &&
			row.category_context &&
			activeDetail === formatDetail('category', row.category)
		)
	);
</script>

<li class="border-t border-line first:border-t-0">
	<div
		class={cn(
			'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0 px-3 pt-0 pb-1 md:gap-y-0.5 md:py-1.5',
			ROW_GRID
		)}
	>
		<!-- 1. Date (desktop column; mobile shows the day header directly above) -->
		<div class="hidden text-caption whitespace-nowrap text-ink-muted tabular-nums md:block">
			{dateLabel}
		</div>

		<!-- 2. Description (who / merchant + recurring + muted what) -->
		<div class="min-w-0">
			<div class="flex min-w-0 items-baseline gap-1.5">
				{#if row.merchant}
					<button
						type="button"
						aria-haspopup="dialog"
						aria-expanded={merchantActive}
						onclick={(event) => ondetail('merchant', row.merchant!, event.currentTarget)}
						title={t('transactions.merchantDetailsTitle', { merchant: row.merchant })}
						class={cn(
							'inline-flex min-h-8 max-w-full min-w-0 items-end pt-2.5 pb-0.5 text-left text-body transition-colors hover:text-brand hover:underline hover:decoration-line-strong hover:underline-offset-2 md:min-h-0 md:items-baseline md:py-0',
							what && 'md:max-w-[65%] md:shrink-0',
							merchantActive ? 'font-medium text-brand' : 'text-ink'
						)}
					>
						<span class="truncate">{who}</span>
					</button>
				{:else}
					<span
						class={cn(
							'min-w-0 truncate pt-2.5 pb-0.5 text-body text-ink md:py-0',
							what && 'md:max-w-[65%] md:shrink-0'
						)}
					>
						{who}
					</span>
				{/if}

				{#if row.recurring}
					<span
						class="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-sunken px-1.5 py-px text-caption text-ink-secondary"
						title={t(
							row.recurring === 'monthly'
								? 'transactions.repeatsMonthlyTitle'
								: 'transactions.repeatsYearlyTitle'
						)}
					>
						<Repeat class="size-2.5" aria-hidden="true" />
						<span class="hidden sm:inline">
							{t(
								row.recurring === 'monthly'
									? 'cashflow.recurringMonthly'
									: 'cashflow.recurringYearly'
							)}
						</span>
					</span>
				{/if}

				{#if what}
					<span class="hidden min-w-0 truncate text-caption text-ink-muted md:inline" title={what}>
						{what}
					</span>
				{/if}
			</div>
			{#if what}
				<p class="truncate text-caption text-ink-muted md:hidden">{what}</p>
			{/if}
		</div>

		<!-- 3. Category (aligned fixed-width column on desktop; row 2 left on mobile) -->
		<div class="order-3 flex min-w-0 items-center gap-0 md:order-none md:gap-1">
			{#if row.category && row.category_context}
				<button
					type="button"
					aria-haspopup="dialog"
					aria-expanded={categoryActive}
					onclick={(event) => ondetail('category', row.category!, event.currentTarget)}
					title={t('transactions.categoryDetailsTitle', { category: row.category })}
					class="group inline-flex min-h-9 max-w-full min-w-0 items-center py-2 text-caption md:min-h-7 md:py-0.5"
				>
					<span
						class={cn(
							'inline-flex max-w-full min-w-0 items-center truncate rounded-full px-2 py-0.5 transition-colors',
							categoryActive
								? 'bg-brand-soft font-medium text-brand'
								: 'bg-sunken text-ink-secondary group-hover:bg-raised group-hover:text-ink'
						)}
					>
						<span class="truncate">{row.category}</span>
					</span>
				</button>
			{:else}
				<span class="inline-flex min-h-9 max-w-full min-w-0 items-center py-2 md:min-h-0 md:py-0">
					<span
						class="inline-flex max-w-full min-w-0 items-center truncate rounded-full bg-sunken/60 px-2 py-0.5 text-caption text-ink-muted"
					>
						<span class="truncate">{row.category ?? kindName(row.kind)}</span>
					</span>
				</span>
			{/if}

			{#if row.changeable && changeable}
				<button
					type="button"
					onclick={(event) => onedit(row, event.currentTarget)}
					title={t('transactions.changeCategoryTitle')}
					aria-label={t('transactions.changeCategoryAria', { category: row.category ?? '' })}
					class="inline-flex size-9 shrink-0 items-center justify-center rounded-control text-ink-muted transition-colors hover:bg-sunken hover:text-ink md:size-7"
				>
					<Pencil class="size-3.5" aria-hidden="true" />
				</button>
			{/if}
		</div>

		<!-- 4. Account (aligned fixed-width column on desktop; row 2 right on mobile) -->
		<div
			class="order-4 flex min-w-0 items-center justify-end gap-1 text-caption text-ink-muted md:order-none md:justify-start"
		>
			<span class="truncate">
				{row.account}{#if people}, {row.owner}{/if}
			</span>
			{#if row.original}
				<a
					href={row.original}
					data-sveltekit-reload
					class="-my-1 -mr-3 -ml-2 inline-flex size-11 shrink-0 items-center justify-center rounded-control text-ink-muted hover:bg-sunken hover:text-brand md:-my-1 md:-mr-1.5 md:-ml-1 md:size-7"
					title={t('transactions.statementFileTitle')}
					aria-label={t('transactions.statementFileAria', { account: row.account })}
				>
					<FileText class="size-3.5" aria-hidden="true" />
				</a>
			{/if}
		</div>

		<!-- 5. Amount (right-aligned with fixed-width currency slot so digits line up) -->
		<div class="order-2 pt-2.5 pb-0.5 text-right md:order-none md:py-0">
			<span
				class={cn('inline-flex items-baseline justify-end whitespace-nowrap tabular-nums', tone)}
			>
				<Amount value={row.amount} class="text-body" />
				<span
					class="ml-1.5 inline-block w-[3.2ch] text-left text-[max(0.6em,0.6875rem)] font-normal text-ink-muted"
				>
					{row.currency}
				</span>
			</span>
		</div>
	</div>
</li>
