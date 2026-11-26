<!--
@component
Every account, one row each (a broker's currencies, booked as an account each, in one row):
its name and status, the days its statements cover on a line of time (committed solid, staged
hatched, a gap left open), and what it holds now. Choosing a row opens its details (balance
over time, statements, typed figures) in the side panel.
-->
<script lang="ts">
	import { page } from '$app/state';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { addDays, day, daysBetween } from '#lib/format/date.js';
	import { getLanguage, t } from '#lib/i18n/index.svelte.js';
	import type { StatementsAccount } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { accountKey, timelineTicks } from './accounts.js';

	interface Props {
		accounts: StatementsAccount[];
		currency: string;
		/** The last day of the line: today. */
		today: string;
		/** Whether several people's accounts are listed: they are then grouped by person. */
		people: boolean;
		/** Key of the account currently shown in the side panel, if any. */
		selected?: string | null;
		/** Called when an account row is chosen. */
		onchoose: (account: StatementsAccount) => void;
		/** Registers each row's button element so closing the panel returns focus cleanly. */
		registerRow?: (key: string, el: HTMLButtonElement | null) => void;
		/** What the list is called; how many accounts it has unless given. */
		title?: string;
	}

	let {
		accounts,
		currency,
		today,
		people,
		selected = null,
		onchoose,
		registerRow,
		title
	}: Props = $props();
	const titleId = $props.id();

	const COLUMNS = 'md:grid-cols-[16rem_minmax(0,1fr)_9rem_1rem]';

	const ranges = $derived([
		{ value: '1y' as const, label: t('range.1y') },
		{ value: '3y' as const, label: t('range.3y') },
		{ value: 'all' as const, label: t('range.all') }
	]);
	type Line = '1y' | '3y' | 'all';
	// Starts from the period chosen on another page when it is one the line offers.
	const carried = page.url.searchParams.get('range');
	let range = $state<Line>(carried === '3y' || carried === 'all' ? carried : '1y');

	const earliest = $derived(
		accounts
			.flatMap((account) => account.spans.map((span) => span.first))
			.reduce((first, each) => (each < first ? each : first), today)
	);
	const start = $derived(
		range === 'all' ? earliest : addDays(today, range === '1y' ? -365 : -1095)
	);
	const length = $derived(Math.max(1, daysBetween(start, today)));
	/** Where `when` falls on the line, in percent, kept on it. */
	const at = (when: string) =>
		Math.min(100, Math.max(0, (daysBetween(start, when) / length) * 100));

	let trackWidth = $state(0);
	const ticks = $derived(timelineTicks(start, today, trackWidth, getLanguage()));

	const groups = $derived(
		people
			? [...new Set(accounts.map((a) => a.owner))].map((owner) => ({
					owner,
					accounts: accounts.filter((a) => a.owner === owner)
				}))
			: [{ owner: null, accounts }]
	);

	function trackButton(node: HTMLButtonElement, key: string) {
		registerRow?.(key, node);
		return {
			update(nextKey: string) {
				if (nextKey !== key) {
					registerRow?.(key, null);
					key = nextKey;
					registerRow?.(key, node);
				}
			},
			destroy() {
				registerRow?.(key, null);
			}
		};
	}
</script>

<section class="mt-10" aria-labelledby={titleId}>
	<header class="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
		<h2 id={titleId} class="text-heading font-medium text-ink">
			{title ??
				(accounts.length === 1
					? t('accounts.countOne')
					: t('accounts.countMany', { count: accounts.length }))}
		</h2>
		<div class="flex flex-wrap items-center gap-4">
			<span class="flex items-center gap-3 text-caption text-ink-muted" aria-hidden="true">
				<span class="flex items-center gap-1.5"
					><span class="h-2 w-4 rounded-mark bg-data-7"></span>{t('accounts.legendCommitted')}</span
				>
				<span class="flex items-center gap-1.5"
					><span class="staged h-2 w-4 rounded-mark"></span>{t('accounts.legendStaged')}</span
				>
			</span>
			<Segmented
				label={t('accounts.timelineAria')}
				value={range}
				options={ranges}
				onchange={(chosen) => (range = chosen)}
			/>
		</div>
	</header>

	<div class="overflow-hidden rounded-panel bg-panel">
		<!-- Column headers: desktop only, aligned with the row and timeline axis grid. -->
		<div
			class={cn(
				'hidden items-center gap-x-4 border-b border-line px-4 py-2 text-caption text-ink-muted md:grid',
				COLUMNS
			)}
			aria-hidden="true"
		>
			<span>{t('accounts.colAccount')}</span>
			<span>{t('accounts.colStatements')}</span>
			<span class="text-right">{t('accounts.colBalance')}</span>
			<span></span>
		</div>

		{#each groups as group (group.owner)}
			{#if group.owner}
				<h3 class="border-b border-line px-4 pt-4 pb-2 text-small text-ink-secondary">
					{group.owner}
				</h3>
			{/if}
			<ul>
				{#each group.accounts as account (accountKey(account))}
					{@const key = accountKey(account)}
					{@const active = selected === key}
					<li class="border-b border-line last:border-b-0">
						<button
							type="button"
							use:trackButton={key}
							aria-haspopup="dialog"
							aria-expanded={active}
							onclick={() => onchoose(account)}
							class={cn(
								'grid w-full grid-cols-[minmax(0,1fr)_auto_1rem] items-center gap-x-4 gap-y-2 px-4 py-3 text-left',
								COLUMNS,
								active ? 'bg-brand-soft' : 'hover:bg-sunken dark:hover:bg-raised'
							)}
						>
							<span class="min-w-0">
								<span class="block truncate font-medium text-ink">{account.account}</span>
								<span
									class={cn(
										'block truncate text-small',
										account.behind ? 'text-caution' : 'text-ink-muted'
									)}
								>
									{#if !account.source}
										{t('accounts.typedOnly')}
									{:else if account.behind}
										{t('accounts.behindEnd', {
											date: account.committed ? day(account.committed) : t('accounts.noDay')
										})}
									{:else if account.committed}
										{t('nav.upTo', { date: day(account.committed) })}{#if account.staged}{t(
												'accounts.stagedTo',
												{ date: day(account.staged) }
											)}{/if}
									{:else}
										{t('accounts.stagedOnly')}
									{/if}
								</span>
							</span>
							<!-- The line of the account's days: hidden on a phone, where the words say it. -->
							<span class="relative hidden h-5 md:block" aria-hidden="true">
								<span class="absolute inset-x-0 top-1/2 h-px bg-line"></span>
								{#each ticks as tick, index (index)}
									<span class="absolute top-0 h-full w-px bg-line/60" style:left="{tick.left}%"
									></span>
								{/each}
								{#each account.spans as run, index (index)}
									{#if run.last >= start}
										<span
											class={cn(
												'absolute top-1 h-3 rounded-mark',
												run.committed ? 'bg-data-7' : 'staged'
											)}
											style:left="{at(run.first)}%"
											style:width="max(3px, {at(run.last) - at(run.first)}%)"
										></span>
									{/if}
								{/each}
							</span>
							<!-- What it holds: the balance figure alone. -->
							<span class="text-right">
								{#if account.balance !== null}
									<Amount
										value={account.balance}
										{currency}
										places={0}
										class="text-title font-medium text-ink"
									/>
								{:else}
									<span class="text-small text-ink-muted">{t('accounts.notKnown')}</span>
								{/if}
							</span>
							<ChevronRight
								class={cn('size-4', active ? 'text-brand' : 'text-ink-muted')}
								aria-hidden="true"
							/>
						</button>
					</li>
				{/each}
			</ul>
		{/each}

		<!-- The months of the line, under the rows, sharing the exact same column grid. -->
		<div
			class={cn('hidden items-center gap-x-4 border-t border-line px-4 md:grid', COLUMNS)}
			aria-hidden="true"
		>
			<span></span>
			<div class="relative h-6" bind:clientWidth={trackWidth}>
				{#each ticks as tick, index (index)}
					{#if tick.label}
						<span
							class={cn(
								'absolute top-1 text-caption whitespace-nowrap text-ink-muted',
								tick.align === 'center' && '-translate-x-1/2',
								tick.align === 'right' && '-translate-x-full'
							)}
							style:left="{tick.left}%"
						>
							{tick.label}
						</span>
					{/if}
				{/each}
			</div>
			<span></span>
			<span></span>
		</div>
	</div>
</section>

<style>
	/* Staged days: hatched, waiting to be committed. */
	.staged {
		background: repeating-linear-gradient(135deg, var(--caution) 0 3px, transparent 3px 6px);
	}
</style>
