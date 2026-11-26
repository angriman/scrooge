<!--
@component
A page or view with nothing to show yet: what is missing, and, where statements can be
imported (`importCta`), the button that goes to `/import`.
-->
<script lang="ts">
	import { resolve } from '$app/paths';
	import { t } from '#lib/i18n/index.svelte.js';

	interface Props {
		heading: string;
		body: string;
		importCta?: boolean;
		level?: 'h1' | 'h2';
	}

	let { heading, body, importCta = false, level = 'h1' }: Props = $props();
</script>

<section class="rounded-lead border border-line bg-panel px-6 py-16 text-center">
	{#if level === 'h2'}
		<h2 class="text-heading font-medium text-ink">{heading}</h2>
	{:else}
		<h1 class="text-heading font-medium text-ink">{heading}</h1>
	{/if}
	<p class="mx-auto mt-2 max-w-prose text-body text-ink-secondary">{body}</p>
	{#if importCta}
		<a
			href={resolve('/import')}
			class="mt-6 inline-flex min-h-11 items-center rounded-control bg-brand px-4 py-2 text-body font-medium text-on-brand hover:bg-brand-hover sm:min-h-0"
		>
			{t('overview.emptyCta')}
		</a>
	{/if}
</section>
