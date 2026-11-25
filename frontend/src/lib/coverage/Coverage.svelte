<!--
@component
Every account a source feeds, one row each (a broker's currencies, booked as an account each, in
one row): what it holds now and a small line of its last
month-ends, how far its statements go, and the days they cover on a line of time, committed
solid, staged hatched, a gap left open. The line is the page's one picture: where a statement is
missing shows as a hole. A row opens to what it holds by part (its cash in each currency, its
securities), then its statements, newest first, each with the file it came from.
-->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import Download from '@lucide/svelte/icons/download';
	import { addDays, day, dayOfYear, daysBetween, monthOf, toDate } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { StatementsAccount } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { cn } from '#lib/ui/cn.js';
	import { heldLines, securitiesQuery } from './holds.js';
	import CloseAccount from './CloseAccount.svelte';
	import { bookedAs } from './parts.js';
	import Sparkline from './Sparkline.svelte';

	interface Props {
		accounts: StatementsAccount[];
		currency: string;
		/** The last day of the line: today. */
		today: string;
		/** Whether several people's accounts are listed: they are then grouped by person. */
		people: boolean;
		/** Take back a committed statement or remove a staged one; none over a copy. */
		onremove?: (statement: StatementsAccount['statements'][number]) => void;
		/** Close an account on a day, or open it again, each with the reason it was refused (null
		 * when done); none over a copy. */
		onclose?: (account: StatementsAccount, day: string) => Promise<string | null>;
		onreopen?: (account: StatementsAccount) => Promise<string | null>;
		/** What the list is called; how many accounts it has unless given. */
		title?: string;
	}

	let { accounts, currency, today, people, onremove, onclose, onreopen, title }: Props = $props();
	const titleId = $props.id();

	const ranges = $derived([
		{ value: '1y' as const, label: t('range.1y') },
		{ value: '3y' as const, label: t('range.3y') },
		{ value: 'all' as const, label: t('range.all') }
	]);
	let range = $state<'1y' | '3y' | 'all'>('1y');

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

	// A tick at the start of every month, or of every year when the line is long; the name of
	// the month (of January only, when long) written under it.
	const ticks = $derived.by(() => {
		const marks: { left: number; label: string | null }[] = [];
		const first = toDate(start);
		const cursor = new Date(first.getFullYear(), first.getMonth() + 1, 1);
		const long = length > 800;
		while (cursor <= toDate(today)) {
			const iso = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}-01`;
			const yearly = cursor.getMonth() === 0;
			if (!long || yearly) {
				marks.push({
					left: at(iso),
					label: long
						? String(cursor.getFullYear())
						: yearly || length <= 400
							? monthOf(cursor)
							: null
				});
			}
			cursor.setMonth(cursor.getMonth() + 1);
		}
		return marks;
	});

	const groups = $derived(
		people
			? [...new Set(accounts.map((a) => a.owner))].map((owner) => ({
					owner,
					accounts: accounts.filter((a) => a.owner === owner)
				}))
			: [{ owner: null, accounts }]
	);

	let open = $state<string | null>(null);
	const keyOf = (account: StatementsAccount) =>
		`${account.owner}|${account.account}|${account.source}`;
	// Where an account's securities are listed: its owner's holdings, in the page's currency.
	const securities = (owner: string) =>
		`${resolve('/investments')}?${securitiesQuery(owner, page.url.searchParams.get('currency'))}`;
	const span = (first: string, last: string) =>
		first.slice(0, 4) === last.slice(0, 4) && first !== last
			? t('accounts.spanTo', { start: dayOfYear(first), end: day(last) })
			: first === last
				? day(first)
				: t('accounts.spanTo', { start: day(first), end: day(last) });
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

	<div class="rounded-panel bg-panel">
		{#each groups as group (group.owner)}
			{#if group.owner}
				<h3 class="border-b border-line px-4 pt-4 pb-2 text-small text-ink-secondary">
					{group.owner}
				</h3>
			{/if}
			<ul>
				{#each group.accounts as account (keyOf(account))}
					{@const key = keyOf(account)}
					{@const expanded = open === key}
					<li class="border-b border-line last:border-b-0">
						<button
							type="button"
							aria-expanded={expanded}
							onclick={() => (open = expanded ? null : key)}
							class="grid w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-4 gap-y-2 px-4 py-3 text-left hover:bg-raised md:grid-cols-[16rem_minmax(0,1fr)_14rem_auto]"
						>
							<span class="min-w-0">
								<span class="block truncate font-medium text-ink">{account.account}</span>
								<span
									class={cn(
										'block truncate text-small',
										account.behind ? 'text-caution' : 'text-ink-muted'
									)}
								>
									{#if account.behind}
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
							<!-- What it holds: the figure of the row, beside the line on a wide screen, after
							     the small line of its last month-ends (on a phone, the figure alone). -->
							<span class="flex items-center justify-end gap-3 text-right md:justify-between">
								{#if account.balance !== null}
									<Amount
										value={account.balance}
										{currency}
										places={0}
										class="text-title font-medium text-ink"
									/>
									{#if account.holds}
										<!-- Drawn before the figure, said after it. -->
										<Sparkline months={account.holds.months} {currency} class="order-first" />
									{/if}
								{:else}
									<span class="text-small text-ink-muted">{t('accounts.notKnown')}</span>
								{/if}
							</span>
							<ChevronDown
								class={cn('size-4 text-ink-muted transition-transform', expanded && 'rotate-180')}
								aria-hidden="true"
							/>
						</button>

						{#if expanded}
							<div class="px-4 pb-4">
								{#if account.holds}
									{@const lines = heldLines(account.holds, currency)}
									{#if lines.length}
										<table class="mb-4 w-full max-w-md text-body">
											<caption class="mb-1 text-left text-small text-ink-secondary"
												>{t('accounts.holdsCaption')}</caption
											>
											<tbody>
												{#each lines as line (line.label)}
													<tr class="border-b border-line last:border-b-0">
														<th
															scope="row"
															class="py-1.5 pr-4 text-left align-top font-normal text-ink-secondary"
														>
															{#if line.securities}
																<a
																	href={securities(account.owner)}
																	class="text-brand hover:text-brand-hover">{line.label}</a
																>
															{:else}
																{line.label}
															{/if}
														</th>
														<td class="py-1.5 text-right">
															<Amount
																value={line.amount}
																currency={line.currency}
																class="text-ink"
															/>
															{#if line.converted !== null}
																<Amount
																	value={line.converted}
																	{currency}
																	class="block text-small text-ink-muted"
																/>
															{/if}
														</td>
													</tr>
												{/each}
											</tbody>
										</table>
									{/if}
								{/if}
								<p class="mb-3 text-small text-ink-muted">
									{account.label}{#if account.parts.length > 1}<span class="block"
											>{bookedAs(account.parts)}</span
										>{/if}{#if !account.monthly}<span class="block">{t('accounts.notMonthly')}</span
										>{/if}
								</p>
								{#if onclose && onreopen}
									<CloseAccount
										{account}
										onclose={(when) => onclose(account, when)}
										onreopen={() => onreopen(account)}
									/>
								{/if}
								<ul class="grid gap-1">
									{#each account.statements as statement (statement.id)}
										<li
											class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-0.5 rounded-control px-2 py-2 hover:bg-raised sm:grid-cols-[14rem_minmax(0,1fr)_auto_auto]"
										>
											<span class="text-body text-ink">{span(statement.first, statement.last)}</span
											>
											<span
												class="order-last truncate text-small text-ink-muted sm:order-none"
												title={statement.filename}
											>
												{statement.filename}
											</span>
											<span class="flex items-center gap-2 text-small whitespace-nowrap">
												<span class={statement.committed ? 'text-ink-secondary' : 'text-caution'}>
													{t(
														statement.committed
															? 'accounts.statusCommitted'
															: 'accounts.statusStaged'
													)}
												</span>
												<span class="text-ink-muted">
													{statement.transactions === 1
														? t('accounts.rowsOne')
														: t('accounts.rowsMany', {
																count: statement.transactions
															})}{#if !statement.checked}{t('accounts.nothingToCheck')}{/if}
												</span>
											</span>
											<span
												class="order-last flex items-center justify-end gap-3 text-small sm:order-none"
											>
												{#if statement.original}
													<a
														href={statement.original}
														data-sveltekit-reload
														class="inline-flex items-center gap-1 text-brand hover:text-brand-hover"
														aria-label={t('accounts.downloadAria', {
															filename: statement.filename
														})}
													>
														<Download class="size-3.5" aria-hidden="true" />{t('accounts.file')}
													</a>
												{/if}
												{#if onremove}
													<button
														type="button"
														onclick={() => onremove(statement)}
														aria-label={t(
															statement.committed ? 'accounts.takeBackAria' : 'accounts.removeAria',
															{ filename: statement.filename }
														)}
														class="text-brand hover:text-brand-hover"
													>
														{t(statement.committed ? 'accounts.takeBack' : 'accounts.remove')}
													</button>
												{/if}
											</span>
										</li>
									{/each}
								</ul>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
		{/each}

		<!-- The months of the line, under the rows: its margins are the columns either side of the
		     line in a row (padding, name, gap; gap, balance, gap, chevron, padding). -->
		<div
			class="relative mx-4 hidden h-6 md:ml-[calc(16rem+2rem)] md:mr-[calc(14rem+4rem)] md:block"
			aria-hidden="true"
		>
			{#each ticks as tick, index (index)}
				{#if tick.label}
					<span
						class="absolute top-1 -translate-x-1/2 text-caption whitespace-nowrap text-ink-muted"
						style:left="{tick.left}%"
					>
						{tick.label}
					</span>
				{/if}
			{/each}
		</div>
	</div>
</section>

<style>
	/* Staged days: hatched, waiting to be committed. */
	.staged {
		background: repeating-linear-gradient(135deg, var(--caution) 0 3px, transparent 3px 6px);
	}
</style>
