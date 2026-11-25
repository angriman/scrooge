<!--
@component
The payments that come back at a steady pace, subscriptions among them: what the active ones
cost a month together, then each, the most a month first, with when it is next due and whether
its price changed. Those that stopped are folded below. Each opens its transactions, when its
name finds them alone.
-->
<script lang="ts">
	import { dayOfYear, day } from '#lib/format/date.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Recurring, RecurringPayment } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import Panel from '#lib/ui/Panel.svelte';
	import { everyName, isNew, priceChange } from './recurring.js';

	interface Props {
		recurring: Recurring;
		/** The address of the transactions with `changes`. */
		transactions: (changes: Record<string, string>) => string;
	}

	let { recurring, transactions }: Props = $props();
	let ended = $state(false);
</script>

{#snippet line(payment: RecurringPayment, stopped: boolean)}
	{@const change = priceChange(payment)}
	<li>
		<!-- A name other payees of the account share would list their rows too: no link then. -->
		<svelte:element
			this={payment.alone ? 'a' : 'div'}
			href={payment.alone
				? transactions({ merchant: payment.name, account: payment.account, owner: payment.owner })
				: undefined}
			class={[
				'grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-0.5 rounded-control px-1 py-2',
				payment.alone && 'hover:bg-sunken'
			]}
		>
			<span class="flex min-w-0 items-center gap-2 text-body text-ink">
				<span class="truncate">{payment.name}</span>
				{#if !stopped && isNew(payment, recurring.through)}
					<span class="shrink-0 rounded-control bg-brand-soft px-1.5 text-caption text-brand"
						>{t('cashflow.recurringNew')}</span
					>
				{/if}
			</span>
			<span class="text-right text-body text-ink">
				<Amount value={payment.amount} currency={payment.currency} />
			</span>
			<span class="truncate text-caption text-ink-muted">
				{t(payment.every === 'monthly' ? 'cashflow.recurringMonthly' : 'cashflow.recurringYearly')} ·
				{stopped
					? t('cashflow.lastPaid', { date: day(payment.last) })
					: t('cashflow.nextDue', { date: dayOfYear(payment.next) })}
			</span>
			<span class="text-right text-caption whitespace-nowrap text-ink-muted">
				{#if change && payment.previous !== null}
					<span class={change === 'up' ? 'text-caution' : 'text-gain'}>
						{change === 'up' ? '▲' : '▼'}
						{t('cashflow.fromPrevious')}
						<Amount value={payment.previous} />
					</span>
				{:else}
					{everyName(payment.every)}
				{/if}
			</span>
		</svelte:element>
	</li>
{/snippet}

<Panel title={t('cashflow.recurringTitle')} note={t('cashflow.recurringNote')}>
	{#each recurring.errors as error (error)}
		<p class="mb-2 text-small text-caution" role="status">{error}</p>
	{/each}
	{#if recurring.active.length}
		<p class="mb-2 text-body text-ink-secondary">
			{#if recurring.monthly !== null}
				<Amount
					value={recurring.monthly}
					places={0}
					currency={recurring.currency}
					class="text-title font-medium text-ink"
				/>
				{t('cashflow.aMonthTogether')}
			{:else}
				{t('cashflow.recurringMissingRate')}
			{/if}
		</p>
		<ul class="divide-y divide-line">
			{#each recurring.active as payment (payment.id)}
				{@render line(payment, false)}
			{/each}
		</ul>
	{:else}
		<p class="text-body text-ink-secondary">{t('cashflow.recurringEmpty')}</p>
	{/if}
	{#if recurring.ended.length}
		<button
			type="button"
			onclick={() => (ended = !ended)}
			aria-expanded={ended}
			class="mt-3 text-small text-brand hover:text-brand-hover"
		>
			{t(ended ? 'cashflow.hideStopped' : 'cashflow.showStopped', {
				count: recurring.ended.length
			})}
		</button>
		{#if ended}
			<ul class="mt-2 divide-y divide-line">
				{#each recurring.ended as payment (payment.id)}
					{@render line(payment, true)}
				{/each}
			</ul>
		{/if}
	{/if}
</Panel>
