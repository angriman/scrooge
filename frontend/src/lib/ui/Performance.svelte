<!--
@component
How much a headline figure moved, as the second thing a reader looks at after the figure
itself: the amount large, in the colour of its direction with an arrow that says it again, and
the percent beside it. What span it is over is the caller's to say, above it.
-->
<script lang="ts">
	import ArrowDownRight from '@lucide/svelte/icons/arrow-down-right';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import { money, signed, signedPercent } from '#lib/format/number.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import type { Amount } from '#lib/types.js';
	import { cn } from './cn.js';

	interface Props {
		amount: Amount | null;
		percent: Amount | null;
		class?: string;
	}

	let { amount, percent, class: className }: Props = $props();

	const written = $derived(amount === null ? null : signed(amount, 0));
	// Which way, as the whole francs written say it: a few cents either way is no change.
	const way = $derived(
		amount === null || money(amount, 0) === '0' ? 'flat' : written!.startsWith('+') ? 'up' : 'down'
	);
</script>

{#if written === null}
	<p class={cn('text-title text-ink-muted', className)}>{t('common.notKnownShorterHistory')}</p>
{:else}
	<p class={cn('flex flex-wrap items-baseline gap-x-3 gap-y-1', className)}>
		<span
			class={cn(
				'inline-flex items-baseline gap-1 text-figure font-medium tracking-tight tabular-nums',
				way === 'up' && 'text-gain',
				way === 'down' && 'text-loss',
				way === 'flat' && 'text-ink-secondary'
			)}
		>
			{#if way === 'up'}<ArrowUpRight
					class="size-6 self-center"
					aria-hidden="true"
				/>{:else if way === 'down'}<ArrowDownRight
					class="size-6 self-center"
					aria-hidden="true"
				/>{/if}
			{written}
		</span>
		{#if percent !== null}
			<span
				class={cn(
					'rounded-control px-2 py-0.5 text-body font-medium tabular-nums',
					way === 'up' && 'bg-gain-soft text-gain',
					way === 'down' && 'bg-loss-soft text-loss',
					way === 'flat' && 'bg-raised text-ink-secondary'
				)}
			>
				{signedPercent(percent)}
			</span>
		{/if}
	</p>
{/if}
