<script lang="ts">
	import Coins from '@lucide/svelte/icons/coins';
	import Image from '@lucide/svelte/icons/image';
	import ListFilter from '@lucide/svelte/icons/list-filter';
	import Tag from '@lucide/svelte/icons/tag';
	import Users from '@lucide/svelte/icons/users';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { t } from '#lib/i18n/index.svelte.js';
	import {
		SETTINGS_SECTIONS,
		activeSection,
		sectionByKey,
		type SettingsSectionKey
	} from '#lib/settings/sections.js';
	import { cn } from '#lib/ui/cn.js';
	import { here, pick } from '#lib/view.js';
	import type { LayoutProps } from './$types';

	let { data, children }: LayoutProps = $props();

	const icons: Record<SettingsSectionKey, typeof Tag> = {
		categories: Tag,
		rules: ListFilter,
		people: Users,
		currencies: Coins,
		logos: Image
	};

	const along = $derived.by(() => {
		const query = pick(here(), ['owner', 'currency', 'range']).toString();
		return query ? `?${query}` : '';
	});

	const current = $derived(activeSection(page.url.pathname, resolve('/')));

	function pickSection(event: Event) {
		const select = event.currentTarget as HTMLSelectElement;
		const chosen = sectionByKey(select.value);
		if (!chosen || chosen.key === current) return;
		void goto(resolve(chosen.path) + along);
	}
</script>

<div class="flex flex-col gap-6">
	<header>
		<h1 class="text-heading font-medium text-ink">{t('settings.title')}</h1>
		<p class="text-body text-ink-secondary">
			{t('settings.subtitle')}{#if data.session.read_only}{t('settings.readOnlySuffix')}{/if}
		</p>
	</header>

	<div class="md:hidden">
		<label class="flex flex-col gap-1 text-caption text-ink-secondary">
			{t('settings.sectionLabel')}
			<select
				aria-label={t('settings.sectionsAria')}
				value={current}
				onchange={pickSection}
				class="h-9 w-full rounded-control border border-line bg-panel px-2.5 text-body text-ink focus:border-brand focus:outline-none"
			>
				{#each SETTINGS_SECTIONS as section (section.key)}
					<option value={section.key}>{t(section.labelKey)}</option>
				{/each}
			</select>
		</label>
	</div>

	<div class="grid grid-cols-1 items-start gap-6 md:grid-cols-[13rem_minmax(0,1fr)]">
		<nav aria-label={t('settings.sectionsAria')} class="hidden md:flex md:flex-col md:gap-1">
			{#each SETTINGS_SECTIONS as section (section.key)}
				{@const Icon = icons[section.key]}
				{@const isCurrent = section.key === current}
				<a
					href={resolve(section.path) + along}
					aria-current={isCurrent ? 'page' : undefined}
					class={cn(
						'flex items-center gap-2 rounded-control px-3 py-2 text-body transition-colors',
						isCurrent
							? 'bg-brand-soft font-medium text-brand'
							: 'text-ink-secondary hover:bg-sunken hover:text-ink'
					)}
				>
					<Icon class="size-4 shrink-0" aria-hidden="true" />
					<span class="truncate">{t(section.labelKey)}</span>
				</a>
			{/each}
		</nav>

		<div class="min-w-0">
			{@render children()}
		</div>
	</div>
</div>
