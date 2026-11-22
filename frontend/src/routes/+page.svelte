<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
	const figures = $derived(data.shown.overview);
</script>

<h1>finanze</h1>

<p>People: {data.session.people.map((person) => person.name).join(', ') || 'none yet'}</p>

{#each data.shown.errors as error}
	<p>{error}</p>
{/each}

{#if figures?.day}
	<p>Net worth on {figures.day}: {figures.total} {figures.currency}</p>
{:else if figures}
	<p>Nothing committed yet.</p>
{/if}

<details>
	<summary>What /api/overview sent</summary>
	<pre>{JSON.stringify(data.shown, null, 2)}</pre>
</details>
