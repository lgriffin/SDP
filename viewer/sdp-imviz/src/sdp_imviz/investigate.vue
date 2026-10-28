<template>
  <j-tray-plugin
    :description="docs_description"
    :uses_active_status="uses_active_status"
    @plugin-ping="plugin_ping($event)"
    :popout_button="popout_button"
    :scroll_to.sync="scroll_to">

    <plugin-viewer-select
      :items="viewer_items"
      :selected.sync="viewer_selected"
      label="Viewer"
      hint="The image viewer whose footprint is the field."
    />

    <plugin-select
      :items="profile_items.map(i => i.label)"
      :selected.sync="profile_selected"
      label="Profile"
      hint="A versioned test profile; its hash is recorded before anything runs."
    />

    <v-row>
      <v-text-field
        v-model="region"
        label="Region (optional)"
        hint="DS9 text such as icrs;circle(120,-10,0.5). Empty uses the viewer footprint."
        persistent-hint
      ></v-text-field>
    </v-row>

    <v-row>
      <v-text-field v-model.number="seed" type="number" label="Seed"></v-text-field>
    </v-row>

    <v-row>
      <v-text-field v-model="label" label="Field label"></v-text-field>
    </v-row>

    <v-row justify="end">
      <plugin-action-button :spinner="spinner" @click="investigate">Investigate</plugin-action-button>
    </v-row>

    <v-row v-if="error">
      <v-alert type="error" style="width: 100%">{{ error }}</v-alert>
    </v-row>

    <v-row v-if="results_available">
      <p>
        <strong>EXPLORATORY.</strong> Set <code>{{ set_id }}</code>: {{ number_of_candidates }} candidate(s).
        Triage and promotion stay on the <code>sdp</code> CLI.
      </p>
    </v-row>

    <jupyter-widget v-if="results_available" :widget="table_widget"></jupyter-widget>
  </j-tray-plugin>
</template>
