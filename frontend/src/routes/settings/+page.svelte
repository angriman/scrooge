<script lang="ts">
	import { tick } from 'svelte';
	import { invalidate } from '$app/navigation';
	import { page } from '$app/state';
	import { errorMessage, send } from '#lib/api.js';
	import { t } from '#lib/i18n/index.svelte.js';
	import AddCategory from '#lib/import/AddCategory.svelte';
	import CategoryRow from '#lib/settings/CategoryRow.svelte';
	import PatternTest from '#lib/settings/PatternTest.svelte';
	import RuleRow from '#lib/settings/RuleRow.svelte';
	import { categoryKey, mergeInto, sections } from '#lib/settings/categories.js';
	import {
		byFile,
		edited,
		fileNote,
		fileTitle,
		moved,
		ruleKey,
		seen,
		stayNote,
		type Draft
	} from '#lib/settings/rules.js';
	import type { Rule } from '#lib/types.js';
	import Panel from '#lib/ui/Panel.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const token = $derived(data.session.token);
	const owner = $derived(page.url.searchParams.get('owner') ?? '');
	const rules = $derived(data.rules);
	const listed = $derived(data.categories);
	const files = $derived(byFile(rules.files, rules.rules));

	// What the box at the top of the rules tries; a rule's "Try" puts its pattern there.
	let match = $state('');
	let direction = $state<Rule['direction']>('');
	let box = $state<HTMLElement>();
	let tester = $state<PatternTest>();

	async function tryPattern(pattern: string, way: Rule['direction']) {
		match = pattern;
		direction = way;
		box?.scrollIntoView({ behavior: 'smooth', block: 'center' });
		await tick();
		await tester?.run();
	}

	/** Send a change, and load the page again when it is made: what went wrong, or null. */
	async function change(path: string, body: unknown): Promise<string | null> {
		const done = await send(`/api/settings/${path}`, body, token);
		if (!done.ok) return errorMessage(done);
		await invalidate('api:settings');
		return null;
	}

	const save = (rule: Rule, draft: Draft) => change('rules/edit', edited(rule, draft));
	const move = (rule: Rule, by: -1 | 1, beside: Rule | undefined) =>
		change('rules/move', moved(rule, by, beside));
	const removeRule = (rule: Rule) => change('rules/remove', seen(rule));
	const add = (name: string, side: string, group: string) =>
		change('categories/add', { name, side, group });
</script>

<svelte:head>
	<title>{t('settings.pageTitle')}</title>
</svelte:head>

<div class="flex flex-col gap-6">
	<header>
		<h1 class="text-heading font-medium text-ink">{t('settings.title')}</h1>
		<p class="text-body text-ink-secondary">
			{t('settings.subtitle')}{#if !rules.editable}{t('settings.readOnlySuffix')}{/if}
		</p>
	</header>

	<div class="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
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
					<AddCategory
						under={listed.groups}
						onadd={add}
						summary={t('settings.addCategorySummary')}
					/>
				{/if}
			</div>
		</Panel>

		<div class="flex flex-col gap-6">
			<Panel
				title={t('settings.rulesTitle')}
				note={owner ? t('settings.rulesNoteOwner', { owner }) : t('settings.rulesNoteAll')}
			>
				{#if rules.editable}<p class="mb-3 text-small text-ink-secondary">{stayNote()}</p>{/if}
				{#each rules.errors as error (error)}
					<p class="mb-2 text-small text-caution" role="status">{error}</p>
				{/each}
				<div bind:this={box} class="mb-4 rounded-panel bg-sunken p-3">
					<PatternTest bind:this={tester} {owner} {token} bind:match bind:direction />
				</div>
				{#if !files.length}
					<p class="text-body text-ink-secondary">{t('settings.noRulesFiles')}</p>
				{/if}
				<div class="flex flex-col gap-5">
					{#each files as group (group.file.key)}
						<section>
							<h3 class="text-body font-medium text-ink">{fileTitle(group.file)}</h3>
							<p class="text-caption text-ink-muted">{fileNote(group.file)}</p>
							{#if group.rules.length}
								<ul class="mt-1 divide-y divide-line">
									{#each group.rules as rule, index (ruleKey(rule))}
										<RuleRow
											{rule}
											leaves={rules.leaves}
											editable={rules.editable}
											first={index === 0}
											last={index === group.rules.length - 1}
											onsave={(draft) => save(rule, draft)}
											onmove={(by) => move(rule, by, group.rules[index + by])}
											onremove={() => removeRule(rule)}
											ontry={tryPattern}
										/>
									{/each}
								</ul>
							{:else}
								<p class="mt-1 text-small text-ink-secondary">{t('settings.noRulesYet')}</p>
							{/if}
						</section>
					{/each}
				</div>
			</Panel>
		</div>
	</div>
</div>
