name: SSVEP-Research-offline
description: Offline SSVEP analysis of the 20260724 three-frequency XDF recording. Segments 0.25-4.75 seconds after each stimulus-onset
  marker, computes one-sided FFT magnitude, and opens one plot per stimulus frequency with eight channel spectra averaged
  across trials.
version: 1.0
nodes:
- uuid: TWTcAcxiOruYA4
  title: Import XDF (offline recording)
  np_class: file_system.ImportXDF
  version: 1.6.0
  position: [100.0, 300.0]
  properties:
    cloud_account: ''
    cloud_bucket: ''
    cloud_credentials: ''
    cloud_host: Default
    file_missing: raise
    filename: C:/Users/MykhailoAntropov/IdeaProjects/turiba/BCI/ssvep-bci/sessions/20260728T165428Z_yehor_k_single-square-big-4min_93a95994.xdf
    handle_clock_resets: true
    handle_clock_sync: true
    handle_jitter_removal: true
    max_marker_len: null
    metadata: {}
    reorder_timestamps: false
    retain_streams: [Cyton EEG, SSVEP Session Events]
    set_breakpoint: false
    use_caching: false
    use_streamnames: true
    verbose: false
  prop_dialog: [779, 224, 1138, 885]
- uuid: ExtractEeg00001
  title: Extract Cyton EEG
  np_class: formatting.ExtractStreams
  version: 2.3.2
  position: [275.0, 225.0]
  properties:
    if_missing: raise
    invert: false
    metadata: {}
    selection_criteria: {}
    selection_operator: AND
    set_breakpoint: false
    stream_names: [Cyton EEG]
    streams: [Cyton EEG]
    support_wildcards: false
    verbose: true
- uuid: ExtractMrk00001
  title: Extract SSVEP session-event markers
  np_class: formatting.ExtractStreams
  version: 2.3.2
  position: [633.0, 472.0]
  properties:
    if_missing: raise
    invert: false
    metadata: {}
    selection_criteria: {}
    selection_operator: AND
    set_breakpoint: false
    stream_names: [SSVEP Session Events]
    streams: [SSVEP Session Events]
    support_wildcards: false
    verbose: true
- uuid: iB8yXeW6nwMZdG
  title: FIR Filter (0.5-1 Hz high-pass transition)
  np_class: signal_processing.FIRFilter
  version: 1.1.2
  position: [450.0, 225.0]
  properties:
    antisymmetric: false
    axis: time
    convolution_method: standard
    cut_preringing: false
    direction: forward
    frequencies: [0.5, 1]
    metadata: {}
    minimum_phase: true
    mode: highpass
    order: null
    set_breakpoint: false
    stop_atten: 60.0
    verbose: false
- uuid: 4LeY03hNYp28tt
  title: Artifact Removal
  np_class: neural.ArtifactRemoval
  version: 2.4.2
  position: [650.0, 225.0]
  properties:
    a: null
    b: null
    block_size: 10
    calib_seconds: 30
    cutoff: 7.5
    emit_calib_data: true
    init_on: []
    lookahead: null
    max_bad_channels: 0.2
    max_dims: 0
    max_dropout_fraction: 0.1
    max_mem: 256
    metadata: {}
    min_clean_fraction: 0.25
    min_required_channels: 2
    preserve_band: null
    riemannian: true
    set_breakpoint: false
    stddev_cutoff: 20
    step_size: 0.2
    use_clean_window: true
    use_legacy: false
    window_len_cleanwindow: 0.5
    window_length: 0.5
    window_overlap: 0.66
    window_overlap_cleanwindow: 0.66
    zscore_thresholds: [-5, 7]
- uuid: gItASSVHyY2Fwa
  title: Remove Bad Time Windows
  np_class: neural.RemoveBadTimeWindows
  version: 1.1.1
  position: [1050.0, 225.0]
  properties:
    max_bad_channels: 0.2
    max_dropout_fraction: 0.1
    metadata: {}
    min_clean_fraction: 0.25
    set_breakpoint: false
    shape_range:
    - 1.7
    - 1.8499999999999999
    - 1.9999999999999998
    - 2.1499999999999995
    - 2.3
    - 2.4499999999999993
    - 2.5999999999999996
    - 2.749999999999999
    - 2.8999999999999995
    - 3.049999999999999
    - 3.1999999999999993
    - 3.3499999999999988
    - 3.499999999999999
    step_sizes: [0.01, 0.01]
    truncate_quantile: [0.022, 0.6]
    window_len: 0.5
    window_overlap: 0.66
    zscore_thresholds: [-4, 6]
- uuid: MergeStreams001
  title: 'Merge Streams

    []'
  np_class: formatting.MergeStreams
  version: 1.0.0
  position: [1225.0, 300.0]
  properties:
    metadata: {}
    replace_if_exists: false
    set_breakpoint: false
    sorting: input
- uuid: RwMkr3Freq001
  title: Map JSON stimulus-onset events to 8/11/14 Hz
  np_class: markers.RewriteMarkers
  version: 0.9.3
  position: [1400.0, 300.0]
  properties:
    iv_column: Marker
    metadata: {}
    pattern_syntax: wildcards
    regex_sub: false
    remove_all_others: true
    rules:
      '*"event_type":"stimulus_onset","marker_code":1000,*': 8 Hz
      '*"event_type":"stimulus_onset","marker_code":1001,*': 11 Hz
      '*"event_type":"stimulus_onset","marker_code":1002,*': 14 Hz
    set_breakpoint: false
- uuid: SegSsvep000001
  title: Segment steady-state window (0.25-4.75 s)
  np_class: formatting.Segmentation
  version: 1.0.5
  position: [1570.0, 438.0]
  properties:
    keep_marker_chunk: false
    max_gap_length: 0.2
    metadata: {}
    online_epoching: marker-locked
    sample_offset: 0
    select_markers: [8 Hz, 11 Hz, 14 Hz]
    set_breakpoint: false
    time_bounds: [0.25, 4.75]
    verbose: true
  prop_dialog: [1530, 681, 1889, 1183]
- uuid: FftSsvep000001
  title: One-sided FFT
  np_class: spectral.FastFourierTransform
  version: 1.0.0
  position: [1704.0, 604.0]
  properties:
    axis: time
    metadata: {}
    n: null
    normalization: forward
    num_partitions: null
    onesided: true
    partition_axis: null
    set_breakpoint: false
- uuid: AbsSsvep000001
  title: FFT Magnitude
  np_class: elementwise_math.Absolute
  version: 1.1.0
  position: [1904.0, 604.0]
  properties:
    metadata: {}
    set_breakpoint: false
  prop_dialog: [1530, 681, 1889, 815]
- uuid: GrpMeanFreq001
  title: Average trials by stimulus frequency
  np_class: statistics.GroupedMean
  version: 1.1.1
  position: [2104.0, 604.0]
  properties:
    allow_markers: false
    ci_range: 0.95
    ddof: 1
    equal_var: false
    error_type: none
    fill_cols: true
    group_cols: [Marker]
    grouping_type: levels
    mean_type: mean
    metadata: {}
    set_breakpoint: false
    sort_order: alphabetical
    trim_proportion: [0, 0]
    use_caching: false
    winsorize: true
  prop_dialog: [1530, 681, 1889, 1492]
- uuid: SelectFreq8Hz01
  title: Select 8 Hz trial mean
  np_class: formatting.SelectInstances
  version: 1.1.3
  position: [2304.0, 354.0]
  properties:
    assign_target_value: null
    combine_previous: override
    combine_selections: and
    condition: ''
    default_comparison_operator: auto
    drop_immediately: true
    invert_selection: false
    metadata: {}
    pred__signature: (Marker)
    selection: 8 Hz
    set_breakpoint: false
    verbose: true
- uuid: SelectFreq11H01
  title: Select 11 Hz trial mean
  np_class: formatting.SelectInstances
  version: 1.1.3
  position: [2304.0, 604.0]
  properties:
    assign_target_value: null
    combine_previous: override
    combine_selections: and
    condition: ''
    default_comparison_operator: auto
    drop_immediately: true
    invert_selection: false
    metadata: {}
    pred__signature: (Marker)
    selection: 11 Hz
    set_breakpoint: false
    verbose: true
- uuid: SelectFreq14H01
  title: Select 14 Hz trial mean
  np_class: formatting.SelectInstances
  version: 1.1.3
  position: [2304.0, 854.0]
  properties:
    assign_target_value: null
    combine_previous: override
    combine_selections: and
    condition: ''
    default_comparison_operator: auto
    drop_immediately: true
    invert_selection: false
    metadata: {}
    pred__signature: (Marker)
    selection: 14 Hz
    set_breakpoint: false
    verbose: true
- uuid: PlotSpectrum8Hz
  title: 8 Hz mean FFT - 8 channels
  np_class: visualization.LegendSpectrumPlot
  version: 1.0.1
  position: [2524.0, 354.0]
  properties:
    always_on_top: false
    annotation_font_size: 11.0
    antialiased: true
    auto_line_colors: true
    autoscale: false
    background_color: '#FFFFFF'
    colormap: gist_rainbow
    decoration_color: '#000000'
    font_size: 11.0
    initial_dims: [10, 30, 620, 500]
    label_rotation: horizontal
    left_offset: 0
    line_color: '#000000'
    line_width: 1.5
    max_channels: 8
    max_redraw_hz: null
    metadata: {}
    one_over_f_correction: false
    plot_minmax: false
    scale: null
    set_breakpoint: false
    show_toolbar: true
    stacked: false
    stream: ''
    stream_name: null
    suppress_window: null
    tight_layout: true
    title: 8 Hz SSVEP - trial-mean FFT for all 8 EEG channels
    track_window_position: false
    unit: data
    verbose: true
    x_label: Frequency (Hz)
    x_range: [5, 17]
    y_label: Mean FFT magnitude
    y_range: [0, 0]
    zero_color: '#D0D0D0'
  prop_dialog: [1500, 479, 1859, 1290]
- uuid: PlotSpectrum11Hz
  title: 11 Hz mean FFT - 8 channels
  np_class: visualization.LegendSpectrumPlot
  version: 1.0.1
  position: [2524.0, 604.0]
  properties:
    always_on_top: false
    annotation_font_size: 11.0
    antialiased: true
    auto_line_colors: true
    autoscale: false
    background_color: '#FFFFFF'
    colormap: gist_rainbow
    decoration_color: '#000000'
    font_size: 11.0
    initial_dims: [650, 30, 620, 500]
    label_rotation: horizontal
    left_offset: 0
    line_color: '#000000'
    line_width: 1.5
    max_channels: 8
    max_redraw_hz: null
    metadata: {}
    one_over_f_correction: false
    plot_minmax: false
    scale: null
    set_breakpoint: false
    show_toolbar: true
    stacked: false
    stream: ''
    stream_name: null
    suppress_window: null
    tight_layout: true
    title: 11 Hz SSVEP - trial-mean FFT for all 8 EEG channels
    track_window_position: false
    unit: data
    verbose: true
    x_label: Frequency (Hz)
    x_range: [5, 17]
    y_label: Mean FFT magnitude
    y_range: [0, 0]
    zero_color: '#D0D0D0'
- uuid: PlotSpectrum14Hz
  title: 14 Hz mean FFT - 8 channels
  np_class: visualization.LegendSpectrumPlot
  version: 1.0.1
  position: [2524.0, 854.0]
  properties:
    always_on_top: false
    annotation_font_size: 11.0
    antialiased: true
    auto_line_colors: true
    autoscale: false
    background_color: '#FFFFFF'
    colormap: gist_rainbow
    decoration_color: '#000000'
    font_size: 11.0
    initial_dims: [1290, 30, 620, 500]
    label_rotation: horizontal
    left_offset: 0
    line_color: '#000000'
    line_width: 1.5
    max_channels: 8
    max_redraw_hz: null
    metadata: {}
    one_over_f_correction: false
    plot_minmax: false
    scale: null
    set_breakpoint: false
    show_toolbar: true
    stacked: false
    stream: ''
    stream_name: null
    suppress_window: null
    tight_layout: true
    title: 14 Hz SSVEP - trial-mean FFT for all 8 EEG channels
    track_window_position: false
    unit: data
    verbose: true
    x_label: Frequency (Hz)
    x_range: [5, 17]
    y_label: Mean FFT magnitude
    y_range: [0, 0]
    zero_color: '#D0D0D0'
links:
- source_node: 4LeY03hNYp28tt
  source_title: Artifact Removal
  sink_node: gItASSVHyY2Fwa
  sink_title: Remove Bad Time Windows
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: AbsSsvep000001
  source_title: FFT Magnitude
  sink_node: GrpMeanFreq001
  sink_title: Average trials by stimulus frequency
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: ExtractEeg00001
  source_title: Extract Cyton EEG
  sink_node: iB8yXeW6nwMZdG
  sink_title: FIR Filter (0.5-1 Hz high-pass transition)
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: ExtractMrk00001
  source_title: Extract SSVEP session-event markers
  sink_node: MergeStreams001
  sink_title: 'Merge Streams

    []'
  source_channel: data
  sink_channel: data2
  enabled: true
- source_node: FftSsvep000001
  source_title: One-sided FFT
  sink_node: AbsSsvep000001
  sink_title: FFT Magnitude
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: GrpMeanFreq001
  source_title: Average trials by stimulus frequency
  sink_node: SelectFreq11H01
  sink_title: Select 11 Hz trial mean
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: GrpMeanFreq001
  source_title: Average trials by stimulus frequency
  sink_node: SelectFreq14H01
  sink_title: Select 14 Hz trial mean
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: GrpMeanFreq001
  source_title: Average trials by stimulus frequency
  sink_node: SelectFreq8Hz01
  sink_title: Select 8 Hz trial mean
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: MergeStreams001
  source_title: 'Merge Streams

    []'
  sink_node: RwMkr3Freq001
  sink_title: Map JSON stimulus-onset events to 8/11/14 Hz
  source_channel: outdata
  sink_channel: data
  enabled: true
- source_node: RwMkr3Freq001
  source_title: Map JSON stimulus-onset events to 8/11/14 Hz
  sink_node: SegSsvep000001
  sink_title: Segment steady-state window (0.25-4.75 s)
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: SegSsvep000001
  source_title: Segment steady-state window (0.25-4.75 s)
  sink_node: FftSsvep000001
  sink_title: One-sided FFT
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: SelectFreq11H01
  source_title: Select 11 Hz trial mean
  sink_node: PlotSpectrum11Hz
  sink_title: 11 Hz mean FFT - 8 channels
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: SelectFreq14H01
  source_title: Select 14 Hz trial mean
  sink_node: PlotSpectrum14Hz
  sink_title: 14 Hz mean FFT - 8 channels
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: SelectFreq8Hz01
  source_title: Select 8 Hz trial mean
  sink_node: PlotSpectrum8Hz
  sink_title: 8 Hz mean FFT - 8 channels
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: TWTcAcxiOruYA4
  source_title: Import XDF (offline recording)
  sink_node: ExtractEeg00001
  sink_title: Extract Cyton EEG
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: TWTcAcxiOruYA4
  source_title: Import XDF (offline recording)
  sink_node: ExtractMrk00001
  sink_title: Extract SSVEP session-event markers
  source_channel: data
  sink_channel: data
  enabled: true
- source_node: gItASSVHyY2Fwa
  source_title: Remove Bad Time Windows
  sink_node: MergeStreams001
  sink_title: 'Merge Streams

    []'
  source_channel: data
  sink_channel: data1
  enabled: true
- source_node: iB8yXeW6nwMZdG
  source_title: FIR Filter (0.5-1 Hz high-pass transition)
  sink_node: 4LeY03hNYp28tt
  sink_title: Artifact Removal
  source_channel: data
  sink_channel: data
  enabled: true
