<!--
@component
Details of one account inside the side panel: its current balance and holdings breakdown,
balance over time chart, statements with file downloads and take-back/remove actions, typed
figures (with inline add and 1-click remove where allowed), and closing/reopening.
-->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import Download from '@lucide/svelte/icons/download';
	import { errorMessage, send } from '#lib/api.js';
	import { day, dayOfYear } from '#lib/format/date.js';
	import { exact } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { StatementFile, StatementsAccount, TypedFigureItem } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { cn } from '#lib/ui/cn.js';
	import BalanceChart from './BalanceChart.svelte';
	import CloseAccount from './CloseAccount.svelte';
	import { heldLines, securitiesQuery } from './holds.js';
	import { bookedAs } from './parts.js';
	import TypedFigure from './TypedFigure.svelte';

	interface Props {
		account: StatementsAccount;
		currency: string;
		today: string;
		currencies: string[];
		/** Token for mutations; null when read-only. */
		token: string | null;
		/** Whether typed figures can be added/removed for this account's owner. */
		canType: boolean;
		onremove?: (statement: StatementFile) => Promise<string | null>;
		onclose?: (day: string) => Promise<string | null>;
		onreopen?: () => Promise<string | null>;
		ontyped: () => void | Promise<void>;
	}

	let {
		account,
		currency,
		today,
		currencies,
		token,
		canType,
		onremove,
		onclose,
		onreopen,
		ontyped
	}: Props = $props();

	let removingTyped = $state<string | null>(null);
	let typedProblem = $state<string | null>(null);
	let removeProblem = $state<string | null>(null);

	const lines = $derived(account.holds ? heldLines(account.holds, currency) : []);
	const securitiesHref = $derived(
		`${resolve('/investments')}?${securitiesQuery(account.owner, page.url.searchParams.get('currency'))}`
	);
	const defaultCurrency = $derived(account.holds?.cash[0]?.currency ?? currency);
	const showTypedSection = $derived(
		account.typed.length > 0 || (canType && account.typable !== 'none')
	);

	const span = (first: string, last: string) =>
		first.slice(0, 4) === last.slice(0, 4) && first !== last
			? t('accounts.spanTo', { start: dayOfYear(first), end: day(last) })
			: first === last
				? day(first)
				: t('accounts.spanTo', { start: day(first), end: day(last) });

	const typedKey = (item: TypedFigureItem) =>
		`${item.account}|${item.day}|${item.what}|${item.symbol ?? ''}|${item.currency}`;

	async function removeTyped(item: TypedFigureItem) {
		if (!token) return;
		const key = typedKey(item);
		removingTyped = key;
		typedProblem = null;
		const done = await send<{ symbol: string | null }>(
			'/api/accounts/typed',
			{
				owner: account.owner,
				account: item.account,
				day: item.day,
				what: item.what,
				symbol: item.symbol ?? '',
				currency: item.currency,
				remove: true
			},
			token
		);
		removingTyped = null;
		if (!done.ok) {
			typedProblem = errorMessage(done);
			return;
		}
		await ontyped();
	}
</script>

<!-- Balance summary and breakdown -->
<div class="mb-5 border-b border-line pb-4">
	<p class="text-figure font-medium tracking-tight text-ink">
		{#if account.balance !== null}
			<Amount value={account.balance} {currency} />
		{:else}
			<span class="text-title text-ink-muted">{t('accounts.notKnown')}</span>
		{/if}
	</p>
	<p class={cn('mt-1 text-small', account.behind ? 'text-caution' : 'text-ink-muted')}>
		{#if !account.source}
			{t('accounts.typedOnly')}
		{:else if account.behind}
			{t('accounts.behindEnd', {
				date: account.committed ? day(account.committed) : t('accounts.noDay')
			})}
		{:else if account.committed}
			{t('nav.upTo', { date: day(account.committed) })}{#if account.staged}{t('accounts.stagedTo', {
					date: day(account.staged)
				})}{/if}
		{:else}
			{t('accounts.stagedOnly')}
		{/if}
	</p>

	{#if lines.length}
		<table class="mt-4 w-full text-body">
			<caption class="sr-only">{t('accounts.holdsCaption')}</caption>
			<tbody>
				{#each lines as line (line.label)}
					<tr class="border-t border-line">
						<th scope="row" class="py-1.5 pr-4 text-left align-top font-normal text-ink-secondary">
							{#if line.securities}
								<a href={securitiesHref} class="text-brand hover:text-brand-hover">{line.label}</a>
							{:else}
								{line.label}
							{/if}
						</th>
						<td class="py-1.5 text-right">
							<Amount value={line.amount} currency={line.currency} class="text-ink" />
							{#if line.converted !== null}
								<Amount value={line.converted} {currency} class="block text-small text-ink-muted" />
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>

<!-- Balance over time -->
{#if account.holds && account.holds.months.length}
	<BalanceChart months={account.holds.months} {currency} />
{/if}

<!-- Statements -->
{#if account.source !== null || account.statements.length}
	<section class="mb-6">
		<div class="mb-2 flex items-baseline justify-between gap-2">
			<h3 class="text-small font-medium text-ink-secondary">{t('accounts.statementsHeading')}</h3>
			<span class="text-caption text-ink-muted tabular-nums">{account.statements.length}</span>
		</div>
		{#if account.parts.length > 1 || !account.monthly}
			<p class="mb-3 text-small text-ink-muted">
				{#if account.parts.length > 1}<span class="block">{bookedAs(account.parts)}</span>{/if}
				{#if !account.monthly}<span class="block">{t('accounts.notMonthly')}</span>{/if}
			</p>
		{/if}
		<ul class="grid gap-1">
			{#each account.statements as statement (statement.id)}
				<li class="rounded-control border border-line/70 px-3 py-2 hover:bg-raised/60">
					<div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
						<span class="text-body text-ink">{span(statement.first, statement.last)}</span>
						<span class="flex items-center gap-2 text-caption whitespace-nowrap">
							<span class={statement.committed ? 'text-ink-secondary' : 'text-caution'}>
								{t(statement.committed ? 'accounts.statusCommitted' : 'accounts.statusStaged')}
							</span>
							<span class="text-ink-muted">
								{statement.transactions === 1
									? t('accounts.rowsOne')
									: t('accounts.rowsMany', {
											count: statement.transactions
										})}{#if !statement.checked}{t('accounts.nothingToCheck')}{/if}
							</span>
						</span>
					</div>
					<div class="mt-1 flex items-center justify-between gap-3">
						<span class="min-w-0 truncate text-caption text-ink-muted" title={statement.filename}>
							{statement.filename}
						</span>
						<span class="flex shrink-0 items-center gap-3 text-small">
							{#if statement.original}
								<a
									href={statement.original}
									data-sveltekit-reload
									class="inline-flex items-center gap-1 text-brand hover:text-brand-hover"
									aria-label={t('accounts.downloadAria', { filename: statement.filename })}
								>
									<Download class="size-3.5" aria-hidden="true" />{t('accounts.file')}
								</a>
							{/if}
							{#if onremove}
								<button
									type="button"
									onclick={async () => {
										removeProblem = null;
										removeProblem = await onremove(statement);
									}}
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
					</div>
				</li>
			{/each}
		</ul>
		{#if removeProblem}
			<p class="mt-2 text-small text-loss" role="alert">{removeProblem}</p>
		{/if}
	</section>
{/if}

<!-- Typed figures -->
{#if showTypedSection}
	<section class="mb-6">
		<div class="mb-2 flex items-baseline justify-between gap-2">
			<h3 class="text-small font-medium text-ink-secondary">{t('accounts.typedHeading')}</h3>
			{#if account.typed.length}
				<span class="text-caption text-ink-muted tabular-nums">{account.typed.length}</span>
			{/if}
		</div>

		{#if account.typed.length}
			<ul class="mb-4 grid gap-1">
				{#each account.typed as item (typedKey(item))}
					{@const key = typedKey(item)}
					<li
						class="flex items-center justify-between gap-3 rounded-control border border-line/70 px-3 py-2"
					>
						<div class="min-w-0">
							<p class="text-body text-ink tabular-nums">
								{#if item.what === 'balance' && item.amount !== null}
									<Amount value={item.amount} currency={item.currency} />
								{:else if item.quantity !== null && item.symbol}
									<span>{exact(item.quantity)} {item.symbol}</span>
									<span class="text-caption text-ink-muted">({item.currency})</span>
								{/if}
							</p>
							<p class="text-caption text-ink-muted">
								{day(item.day)}{#if account.parts.length > 1}
									· {item.account}{/if}
							</p>
						</div>
						{#if canType && token}
							<button
								type="button"
								disabled={removingTyped === key}
								onclick={() => removeTyped(item)}
								aria-label={t('accounts.removeTypedAria', {
									account: item.account,
									day: day(item.day)
								})}
								class="shrink-0 text-small text-brand hover:text-brand-hover disabled:opacity-60"
							>
								{t('accounts.remove')}
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="mb-3 text-small text-ink-muted">{t('accounts.typedEmpty')}</p>
		{/if}

		{#if typedProblem}
			<p class="mb-3 text-small text-loss" role="alert">{typedProblem}</p>
		{/if}

		{#if canType && token && account.typable !== 'none'}
			<div class="rounded-control border border-line bg-raised/30 p-3">
				<p class="mb-2 text-caption font-medium text-ink-secondary">{t('typed.addHeading')}</p>
				<TypedFigure
					owners={[account.owner]}
					owner={account.owner}
					accounts={account.parts}
					{currencies}
					{today}
					{token}
					fixedOwner={account.owner}
					fixedAccounts={account.parts}
					allowPosition={account.typable === 'both'}
					{defaultCurrency}
					showRemove={false}
					ondone={ontyped}
				/>
			</div>
		{/if}
	</section>
{/if}

<!-- Close / reopen account -->
{#if onclose && onreopen}
	<div class="border-t border-line pt-4">
		<CloseAccount {account} {onclose} {onreopen} />
	</div>
{/if}
