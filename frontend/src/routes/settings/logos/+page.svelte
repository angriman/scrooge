<script lang="ts">
	import { invalidate } from '$app/navigation';
	import { assetColor, assetName } from '#lib/design/data.js';
	import LogoControl from '#lib/holdings/LogoControl.svelte';
	import { t } from '#lib/i18n/index.svelte.js';
	import Panel from '#lib/ui/Panel.svelte';
	import Swatch from '#lib/ui/Swatch.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const listed = $derived(data.logos);
	const withLogo = $derived(
		listed.instruments.filter((instrument) => Boolean(listed.logos[instrument.symbol])).length
	);
</script>

<svelte:head>
	<title>{t('settings.subpageTitle', { section: t('settings.sectionLogos') })}</title>
</svelte:head>

<Panel title={t('settings.logosTitle')} note={t('settings.logosNote')}>
	{#snippet actions()}
		{#if listed.instruments.length}
			<span class="text-caption text-ink-muted tabular-nums">
				{t('settings.logosSummary', { withLogo, total: listed.instruments.length })}
			</span>
		{/if}
	{/snippet}

	{#each listed.errors as error (error)}
		<p class="mb-2 text-small text-caution" role="status">{error}</p>
	{/each}

	{#if !listed.instruments.length}
		<p class="text-body text-ink-secondary">{t('settings.logosEmpty')}</p>
	{:else}
		<ul class="divide-y divide-line">
			{#each listed.instruments as instrument (instrument.symbol)}
				{@const logoUrl = listed.logos[instrument.symbol]}
				{@const color = assetColor(instrument.asset)}
				<li class="py-3 first:pt-0 last:pb-0">
					<div class="flex flex-wrap items-center justify-between gap-3">
						<div class="flex min-w-0 items-center gap-3">
							{#if logoUrl}
								<img
									src={logoUrl}
									alt=""
									class="size-8 shrink-0 rounded-control bg-ink object-contain p-0.5"
								/>
							{:else}
								<span
									aria-hidden="true"
									class="inline-flex size-8 shrink-0 items-center justify-center rounded-control border border-line bg-sunken text-caption font-medium text-ink-secondary"
								>
									{instrument.symbol.slice(0, 2).toUpperCase()}
								</span>
							{/if}
							<div class="min-w-0">
								<p class="truncate font-medium text-ink">
									{instrument.name || instrument.symbol}
								</p>
								<p class="flex flex-wrap items-center gap-1.5 text-caption text-ink-muted">
									<Swatch {color} class="size-2 rounded-full" />
									<span class="text-ink-secondary">{assetName(instrument.asset)}</span>
									<span>{instrument.symbol}</span>
									{#if instrument.currencies.length}
										<span>· {instrument.currencies.join(', ')}</span>
									{/if}
								</p>
							</div>
						</div>

						{#if !listed.editable}
							<span class="text-caption text-ink-muted">
								{logoUrl ? t('settings.logosHasLogo') : t('settings.logosNoLogo')}
							</span>
						{/if}
					</div>

					{#if listed.editable}
						<LogoControl
							symbol={instrument.symbol}
							has={Boolean(logoUrl)}
							token={data.session.token}
							onchange={() => invalidate('api:settings:logos')}
						/>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	{#if !listed.editable}
		<p class="mt-4 text-caption text-ink-muted">{t('settings.logosReadOnlyHint')}</p>
	{/if}
</Panel>
