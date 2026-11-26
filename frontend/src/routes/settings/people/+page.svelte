<script lang="ts">
	import { day } from '#lib/format/date.js';
	import { joinList, t } from '#lib/i18n/index.svelte.js';
	import Panel from '#lib/ui/Panel.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const listed = $derived(data.people);
</script>

<svelte:head>
	<title>{t('settings.subpageTitle', { section: t('settings.sectionPeople') })}</title>
</svelte:head>

<Panel title={t('settings.peopleTitle')} note={t('settings.peopleNote')}>
	{#each listed.errors as error (error)}
		<p class="mb-2 text-small text-caution" role="status">{error}</p>
	{/each}

	{#if !listed.people.length}
		<p class="text-body text-ink-secondary">{t('settings.peopleEmpty')}</p>
	{:else}
		<div class="flex flex-col divide-y divide-line">
			{#each listed.people as person (person.name)}
				{@const openAccounts = person.accounts.filter((account) => account.closed === null)}
				{@const closedAccounts = person.accounts.filter((account) => account.closed !== null)}
				<section class="py-4 first:pt-0 last:pb-0">
					<h3 class="text-title font-medium text-ink">{person.name}</h3>

					<div class="mt-2">
						<p class="text-caption font-medium text-ink-secondary">
							{t('settings.peoplePatterns')}
						</p>
						{#if person.patterns.length}
							<div class="mt-1 flex flex-wrap gap-1.5">
								{#each person.patterns as pattern (pattern)}
									<code
										class="rounded-control border border-line bg-sunken px-2 py-0.5 font-mono text-caption text-ink"
									>
										{pattern}
									</code>
								{/each}
							</div>
						{:else}
							<p class="mt-1 text-small text-ink-muted">{t('settings.peopleNoPatterns')}</p>
						{/if}
					</div>

					<div class="mt-3">
						<p class="text-caption font-medium text-ink-secondary">
							{t('settings.peopleAccounts')}
						</p>
						{#if !person.accounts.length}
							<p class="mt-1 text-small text-ink-muted">{t('settings.peopleNoAccounts')}</p>
						{:else}
							{#if openAccounts.length}
								<ul class="mt-1 divide-y divide-line">
									{#each openAccounts as account (`${account.name}|${account.source ?? 'typed'}`)}
										<li
											class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-1.5"
										>
											<div class="min-w-0">
												<span class="text-body text-ink">{account.name}</span>
												{#if account.parts.length > 1}
													<span class="ml-1.5 text-caption text-ink-muted">
														{t('accounts.bookedAs', { list: joinList(account.parts) })}
													</span>
												{/if}
											</div>
											<span class="text-caption text-ink-muted">
												{account.label ?? t('settings.peopleTypedAccount')}
											</span>
										</li>
									{/each}
								</ul>
							{/if}

							{#if closedAccounts.length}
								<p class="mt-3 text-caption font-medium text-ink-muted">
									{t('settings.peopleClosedAccounts')}
								</p>
								<ul class="mt-1 divide-y divide-line">
									{#each closedAccounts as account (`${account.name}|${account.source ?? 'typed'}`)}
										<li
											class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-1.5"
										>
											<div class="min-w-0">
												<span class="text-body text-ink-secondary">{account.name}</span>
												{#if account.parts.length > 1}
													<span class="ml-1.5 text-caption text-ink-muted">
														{t('accounts.bookedAs', { list: joinList(account.parts) })}
													</span>
												{/if}
											</div>
											<span class="text-caption text-ink-muted">
												{account.label ?? t('settings.peopleTypedAccount')}
												{#if account.closed}
													· {t('settings.peopleClosedOn', { date: day(account.closed) })}
												{/if}
											</span>
										</li>
									{/each}
								</ul>
							{/if}
						{/if}
					</div>
				</section>
			{/each}
		</div>
	{/if}

	<p class="mt-4 text-caption text-ink-muted">{t('settings.peopleConfigHint')}</p>
</Panel>
