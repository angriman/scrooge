<!--
@component
What the transactions are cut to, over the last two years: one category, merchant or account,
a bar for each month against its usual month (the dashed line), the last month lighter while
it is not over. Beside it, where most of a category was spent, each a link to its rows, or
what an account holds now. A month pointed at is said in full beneath.
-->
<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { month, monthName } from '#lib/format/date.js';
	import type { TransactionsContext } from '#lib/types.js';
	import Amount from '#lib/ui/Amount.svelte';
	import { draw } from './context.js';

	interface Props {
		context: TransactionsContext;
		/** The address of the transactions of a merchant. */
		merchant: (name: string) => string;
	}

	let { context, merchant }: Props = $props();

	const KINDS = { category: 'Category', merchant: 'Merchant', account: 'Account' } as const;
	const HEIGHT = 64;

	let width = $state(480);
	const drawn = $derived(draw(context.months, context.usual, context.partial, width, HEIGHT));
	const first = $derived(context.months[0]);
	const last = $derived(context.months.at(-1));
	const verb = $derived(context.earned ? 'Earned' : 'Spent');

	let shown = $state<number | null>(null);
	const pointed = $derived(shown === null ? null : context.months[shown]);
</script>

<section class="rounded-panel bg-panel p-4" aria-label="{context.name} over the last two years">
	<header class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
		<div class="min-w-0">
			<p class="text-caption text-ink-muted">{KINDS[context.kind]}, last two years</p>
			<h2 class="truncate text-title font-medium text-ink">{context.name}</h2>
		</div>
		{#if context.balance !== null}
			<p class="text-small text-ink-secondary">
				Holds <Amount value={context.balance} currency={context.currency} class="text-body text-ink" /> now
			</p>
		{/if}
	</header>

	{#each context.errors as error (error)}
		<p class="mt-3 flex items-start gap-2 text-small text-ink" role="status">
			<CircleAlert class="mt-0.5 size-4 shrink-0 text-caution" aria-hidden="true" />
			{error}
		</p>
	{/each}

	{#if first && last}
		<div class="mt-3 grid gap-x-6 gap-y-4 md:grid-cols-[minmax(0,1fr)_16rem]">
			<div class="min-w-0">
				<div bind:clientWidth={width}>
					<svg
						viewBox="0 0 {width} {HEIGHT}"
						class="block h-16 w-full overflow-visible"
						role="img"
						aria-label="{verb} each month from {month(`${first.month}-01`)} to {month(`${last.month}-01`)}{context.usual ===
						null
							? ''
							: ', against the usual month'}"
					>
						{#each drawn.bars as bar, index (bar.month)}
							<g
								role="presentation"
								onpointerenter={() => (shown = index)}
								onpointerleave={() => (shown = null)}
								opacity={shown === null || shown === index ? 1 : 0.45}
							>
								<rect x={bar.x - (width / drawn.bars.length - bar.width) / 2} y="0" width={width / drawn.bars.length} height={HEIGHT} fill="transparent" />
								{#if bar.height > 0}
									<rect
										x={bar.x}
										y={bar.y}
										width={bar.width}
										height={bar.height}
										rx="1"
										class={bar.partial ? 'fill-brand/35 stroke-brand' : 'fill-brand'}
										stroke-dasharray={bar.partial ? '2 2' : undefined}
										stroke-width={bar.partial ? 1 : 0}
									/>
								{/if}
							</g>
						{/each}
						<line x1="0" x2={width} y1={HEIGHT - 0.5} y2={HEIGHT - 0.5} class="stroke-line" />
						{#if drawn.usual !== null}
							<line
								x1="0"
								x2={width}
								y1={drawn.usual}
								y2={drawn.usual}
								class="stroke-ink-secondary"
								stroke-dasharray="4 3"
								pointer-events="none"
							/>
						{/if}
					</svg>
				</div>
				<div class="mt-1 flex justify-between text-caption text-ink-muted">
					<span>{month(`${first.month}-01`)}</span>
					<span>{month(`${last.month}-01`)}{context.partial ? ', so far' : ''}</span>
				</div>

				<p class="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-small text-ink-secondary" aria-live="polite">
					{#if pointed}
						<span>
							{verb} in {monthName(pointed.month)}{context.partial && pointed === last ? ' so far' : ''}
							<Amount value={pointed.amount} currency={context.currency} places={0} class="text-body text-ink" />
						</span>
					{:else}
						<span>
							{verb} in {monthName(last.month)}{context.partial ? ' so far' : ''}
							<Amount value={last.amount} currency={context.currency} places={0} class="text-body text-ink" />
						</span>
					{/if}
					<span class="inline-flex items-baseline gap-1.5">
						<span class="inline-block w-3 border-t border-dashed border-ink-secondary" aria-hidden="true"></span>
						{#if context.usual === null}
							Usual month not known yet
						{:else}
							Usual month <Amount value={context.usual} currency={context.currency} places={0} class="text-body text-ink" />
						{/if}
					</span>
				</p>
			</div>

			{#if context.merchants?.length}
				<div>
					<h3 class="text-caption text-ink-muted">{context.earned ? 'Most came from' : 'Most was spent at'}</h3>
					<ul class="mt-1 divide-y divide-line">
						{#each context.merchants as payee (payee.name)}
							<li>
								<a
									href={merchant(payee.name)}
									class="flex items-baseline gap-3 rounded-control px-1 py-1.5 hover:bg-sunken"
								>
									<span class="min-w-0 flex-1 truncate text-body text-ink">{payee.name}</span>
									<span class="text-caption text-ink-muted">{payee.count}×</span>
									<Amount value={payee.amount} places={0} class="text-body text-ink" />
								</a>
							</li>
						{/each}
					</ul>
				</div>
			{/if}
		</div>
	{:else if !context.errors.length}
		<p class="mt-2 text-small text-ink-secondary">Nothing is committed yet to show it over time.</p>
	{/if}
</section>
