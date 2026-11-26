<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { errorMessage, send } from '#lib/api.js';
	import { getLanguage, setLanguage, t, type Language } from '#lib/i18n/index.svelte.js';
	import Panel from '#lib/ui/Panel.svelte';
	import Segmented from '#lib/ui/Segmented.svelte';
	import { cn } from '#lib/ui/cn.js';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const settings = $derived(data.currencies);
	let problem = $state<string | null>(null);

	const languageNames: Record<Language, () => string> = {
		en: () => t('settings.languageEn'),
		it: () => t('settings.languageIt')
	};

	const languageOptions = $derived(
		settings.languages.map((lang) => ({
			value: lang,
			label: `${lang.toUpperCase()} · ${languageNames[lang]()}`
		}))
	);

	async function chooseLanguage(chosen: Language) {
		if (chosen === getLanguage()) return;
		problem = null;
		const done = await send('/api/language', { language: chosen }, data.session.token);
		if (!done.ok) {
			problem = errorMessage(done);
			return;
		}
		setLanguage(chosen);
		await invalidateAll();
	}
</script>

<svelte:head>
	<title>{t('settings.subpageTitle', { section: t('settings.sectionCurrencies') })}</title>
</svelte:head>

<div class="flex flex-col gap-6">
	<Panel title={t('settings.languageTitle')} note={t('settings.languageNote')}>
		<div class="flex flex-wrap items-center gap-3">
			<Segmented
				label={t('settings.languageLabel')}
				value={getLanguage()}
				options={languageOptions}
				onchange={chooseLanguage}
			/>
		</div>
		{#if problem}
			<p class="mt-2 text-small text-loss" role="alert">{problem}</p>
		{/if}
	</Panel>

	<Panel title={t('settings.currenciesTitle')} note={t('settings.currenciesNote')}>
		{#each settings.errors as error (error)}
			<p class="mb-2 text-small text-caution" role="status">{error}</p>
		{/each}

		<dl class="divide-y divide-line">
			<div class="flex flex-wrap items-baseline justify-between gap-2 py-2.5 first:pt-0">
				<dt class="text-body text-ink-secondary">{t('settings.currencyDefault')}</dt>
				<dd class="font-medium text-ink">{settings.currency}</dd>
			</div>

			<div class="flex flex-wrap items-baseline justify-between gap-2 py-2.5">
				<dt class="text-body text-ink-secondary">{t('settings.currencyOffered')}</dt>
				<dd class="flex flex-wrap gap-1.5">
					{#each settings.currencies as code (code)}
						<span
							class={cn(
								'rounded-control border px-2 py-0.5 text-small',
								code === settings.currency
									? 'border-brand bg-brand-soft font-medium text-brand'
									: 'border-line bg-sunken text-ink'
							)}
						>
							{code}
						</span>
					{/each}
				</dd>
			</div>

			<div class="flex flex-wrap items-baseline justify-between gap-2 py-2.5 last:pb-0">
				<dt class="text-body text-ink-secondary">{t('settings.currencyTable')}</dt>
				<dd class="flex flex-wrap gap-1.5">
					{#each settings.table_currencies as code (code)}
						<span
							class="rounded-control border border-line bg-sunken px-2 py-0.5 text-small text-ink"
						>
							{code}
						</span>
					{/each}
				</dd>
			</div>
		</dl>

		<p class="mt-4 text-caption text-ink-muted">{t('settings.currencyConfigHint')}</p>
	</Panel>
</div>
