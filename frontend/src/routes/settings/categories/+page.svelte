<script lang="ts">
	import { invalidate } from '$app/navigation';
	import { errorMessage, send } from '#lib/api.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import AddCategory from '#lib/import/AddCategory.svelte';
	import CategoryRow from '#lib/settings/CategoryRow.svelte';
	import { categoryKey, mergeInto, sections } from '#lib/settings/categories.js';
	import Panel from '#lib/ui/Panel.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const token = $derived(data.session.token);
	const listed = $derived(data.categories);

	async function change(path: string, body: unknown): Promise<string | null> {
		const done = await send(`/api/settings/${path}`, body, token);
		if (!done.ok) return errorMessage(done);
		await invalidate('api:settings:categories');
		return null;
	}

	const add = (name: string, side: string, group: string) =>
		change('categories/add', { name, side, group });
</script>

<svelte:head>
	<title>{t('settings.subpageTitle', { section: t('settings.sectionCategories') })}</title>
</svelte:head>

<Panel title={t('settings.categoriesTitle')} note={t('settings.categoriesNote')}>
	{#each listed.errors as error (error)}
		<p class="mb-2 text-small text-caution" role="status">{error}</p>
	{/each}
	<div class="flex flex-col gap-4">
		{#each sections(listed.categories) as section (section.heading)}
			<section>
				<h3 class="text-caption font-medium text-ink-secondary">{section.heading}</h3>
				<ul class="divide-y divide-line">
					{#each section.categories as category (categoryKey(category))}
						<CategoryRow
							{category}
							into={mergeInto(listed.categories, category)}
							groups={listed.groups}
							unmatched={listed.unmatched}
							editable={listed.editable}
							onrename={(to) => change('categories/rename', { name: category.name, to })}
							onmerge={(into) => change('categories/merge', { name: category.name, into })}
							onmove={(under) => change('categories/move', { name: category.name, under })}
							onremove={() => change('categories/remove', { name: category.name })}
						/>
					{/each}
				</ul>
			</section>
		{/each}
		{#if listed.editable}
			<AddCategory under={listed.groups} onadd={add} summary={t('settings.addCategorySummary')} />
		{/if}
	</div>
</Panel>
