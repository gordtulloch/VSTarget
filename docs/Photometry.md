# How VSTarget Measures a Variable Star's Brightness

This document walks through what the **Analyze** function does and — just as
importantly — *why* each step is necessary. It assumes no prior experience
with photometry. The implementation lives in
[photometry.py](../photometry.py) (`run_photometry()`) and mirrors the
reference notebook [notebooks/RWAUR.ipynb](../notebooks/RWAUR.ipynb). Every run
writes a plain-text log (`photometry_<timestamp>_<star>.log`) to the working
directory recording the actual numbers at each of the steps below, so you can
follow a real measurement alongside this explanation.

## The problem being solved

A CCD image doesn't record magnitudes — it records *counts*: how many
electrons each pixel collected. The number of counts a star produces depends
on the star's true brightness, but also on the telescope's aperture, the
filter, the exposure time, the camera's sensitivity, how high the star was in
the sky, and how clear the air was *at that moment*. Most of those factors
change from night to night and even minute to minute. Counts alone therefore
tell you almost nothing about how bright the star really is.

**Differential photometry** sidesteps the whole problem with one observation:
every star in the same image was photographed through the same telescope, the
same filter, the same exposure, and essentially the same patch of atmosphere.
Whatever those factors did to the target star, they did equally to its
neighbours. So instead of asking *"how bright is my star?"* we ask *"how
bright is my star **compared to** nearby stars whose brightness is already
known and constant?"* — and all the unknown factors cancel out.

The stars of known, constant brightness are called **comparison stars**
("comps"), and the AAVSO maintains carefully calibrated lists of them around
every variable star precisely for this purpose.

With that idea in mind, here is what the pipeline does.

## Step 1 – Read the image

The FITS file is loaded along with its header. The header supplies context the
calculation needs later:

- **OBJECT** and **FILTER** – which star we think this is, and which
  photometric band the image was taken in. The band matters because a star's
  magnitude is different in different colours; we must compare our measurement
  against catalog magnitudes *in the same band*.
- **DATE-OBS** – when the image was taken, converted to a **Julian Date**.
  Variable star astronomy runs on JD because it is a single continuously
  increasing number, immune to time zones and calendar arithmetic; a light
  curve is magnitude plotted against JD.
- **AIRMASS** – how much atmosphere the light passed through (1.0 = straight
  overhead). It is recorded in the final report for reference; it does not
  enter the calculation, because the differential method already cancels
  atmospheric dimming.
- The **WCS** (World Coordinate System) — the mapping between sky coordinates
  and pixel positions written by plate solving. Everything that follows
  depends on it, which is why the Images panel insists on plate-solved frames.

## Step 2 – Find the target's sky coordinates

We know the star's *name*, but to find it in the image we need its RA/Dec.
The name is resolved through the Simbad astronomical database. If that fails
(no network, unrecognised name), the pipeline assumes the telescope centred
the target and uses the middle of the field as a fallback.

*Why not just click on the star?* Automation and repeatability: given a
plate-solved image and a name, the measurement involves no human judgement
that could vary from night to night.

## Step 3 – Download the comparison stars

The pipeline queries the AAVSO Variable Star Plotter (VSP) API for the
photometric sequence around the target (18.5′ field, magnitude limit 18.5) and
keeps every star that has a calibrated magnitude in the image's filter band.

*Why these stars specifically?* Not any field star will do. A useful
comparison star must be (a) **non-variable** — otherwise you'd measure its
changes as much as your target's — and (b) **accurately calibrated** in the
standard magnitude system. AAVSO sequence teams have vetted these stars for
both. Each has an identifier (**AUID**) and the query returns a **chart ID**,
which is kept in the final report so anyone can later reconstruct exactly
which reference stars your measurement relied on.

## Step 4 – Detect the stars and remove the sky background

Before measuring anything, two things must happen:

**The sky background is modelled and subtracted.** Even "empty" sky isn't
dark: moonlight, airglow, and light pollution add a pedestal of counts to
every pixel, and it varies smoothly across the frame. If we summed a star's
pixels without removing this pedestal, the sky would be counted as starlight
and every measurement would be too bright — by an amount that changes with
the Moon and the weather. The pipeline builds a smooth 2-D model of the
background (with **SEP**, or photutils' `Background2D` if SEP is not
installed) and subtracts it, so that what remains is starlight alone.

**Sources are detected.** The pipeline finds everything in the image that
stands out from the background noise by at least a chosen factor (default
**SNR ≥ 5**, i.e. five times the background scatter). The threshold exists to
separate real stars from noise: set it too low and random noise peaks get
treated as stars; too high and faint comparison stars are lost.

## Step 5 – Match catalog stars to detected stars

Each comparison star's RA/Dec (and the target's) is converted to a predicted
pixel position through the WCS. If a detected source lies within ±5 pixels of
the prediction, the star is *matched*.

*Why match at all, instead of just measuring at the predicted position?*
The match confirms the star is actually there and detectable. A comparison
star can drop out for good reasons — outside the frame, too faint for
tonight's conditions, hopelessly saturated — and it's better to exclude it
than to measure noise (or a bleed trail) at its coordinates. Unmatched stars
are listed in the log. If the *target* fails to match, the run stops with an
error: either the image isn't really solved, or the star isn't in the field —
no measurement is better than a wrong one.

## Step 6 – Measure raw brightness (instrumental magnitudes)

A circle of fixed radius (default **6 px**, the *aperture*) is placed over
every matched star and the background-subtracted counts inside it are summed.
That sum $F$ is the star's flux in camera units. It is converted to a
logarithmic scale:

$$m_{\text{inst}} = -2.5 \,\log_{10} F$$

*Why the strange formula?* Magnitudes are logarithmic by ancient convention
(each 5 magnitudes = exactly 100× in brightness, and *smaller* numbers mean
*brighter*). Converting counts to the same logarithmic scale as catalog
magnitudes makes the next step a simple straight-line fit, because a ratio of
fluxes becomes a *difference* of magnitudes.

These are called **instrumental magnitudes** because they are on an arbitrary
scale unique to this image — shift the exposure time or the telescope and
every value changes by the same offset. On their own they are meaningless;
their *differences* between stars in the same frame are the physically
meaningful quantity.

*Why the same aperture for every star?* Consistency. Point stars all have the
same light profile in a given image, so a fixed circle captures the same
*fraction* of every star's light. Any light the aperture misses is missed
equally for target and comps, and cancels in the differential step.

## Step 7 – The ensemble fit (the heart of the calculation)

Now we know, for each comparison star, both its **instrumental** magnitude
(measured in step 6) and its **catalog** magnitude (from step 3). What we need
is the rule that converts one scale into the other.

First, the comps are filtered to a magnitude window (default catalog mag
**11.0–13.5**). *Why exclude stars?* At the bright end, stars may saturate
the detector — their brightest pixels hit the sensor's ceiling, counts are
lost, and the measurement reads falsely faint. At the faint end, stars are
dominated by noise. Both would corrupt the calibration. At least two comps
must survive the cut or the run aborts.

Then a straight line is fitted through the surviving $N$ stars:

$$V_{\text{cat}} = a \cdot m_{\text{inst}} + b$$

*Why should a straight line work?* Because for an ideal linear detector the
two scales differ only by a constant offset — the **zero point** — which
bundles up everything the differential method cancels: exposure, telescope
throughput, atmospheric dimming. In that ideal case the slope $a$ is exactly
**1** and the intercept $b$ is the zero point, and the fit is equivalent to
the classic textbook form of ensemble photometry (target instrumental
magnitude plus the average catalog-minus-instrumental offset of the comps):

$$V_{\text{target}} = m_{\text{inst,target}} + \overline{\left(V_{\text{cat}} - m_{\text{inst}}\right)}$$

Letting the slope float instead of pinning it to 1 gives the fit room to
absorb mild detector non-linearity. The fitted slope is written to the log,
and it doubles as a health check: a slope far from 1.0 means something is
wrong — saturated comps, poor background subtraction, too few stars.

*Why an ensemble instead of a single comparison star?* Any one star's
measurement carries noise, and any one catalog value carries a small error.
Averaging over many stars beats down both. It also produces an honest error
estimate for free — see below.

**The uncertainty.** The quoted error is the RMS scatter of the comps around
the fitted line:

$$\sigma = \sqrt{\tfrac{1}{N}\sum_i \left(V_{\text{cat},i} - V_{\text{fit},i}\right)^2}$$

The logic: the comparison stars are constant, so if our method were perfect
they would land *exactly* on the line. However far they actually scatter
tells us how precise a single measurement made this way is — and the target
was measured the same way, so the same scatter applies to it. This value is
reported as **MERR**. (It is a scatter statistic, not a formal propagation of
each star's photon noise and catalog error individually.)

## Step 8 – The target's magnitude

The target's instrumental magnitude is plugged into the fitted line:

$$V_{\text{target}} = a \cdot m_{\text{inst,target}} + b$$

That number, with the RMS above, is the observation — e.g.
`V = 9.818 ± 0.014` for AG Dra in the README screenshots.

One honest caveat: if the target is *brighter* than the comparison window
(common for bright variables — AG Dra at ~9.8 vs comps at 11–13.5), the line
is being extended beyond the range of stars that defined it
(**extrapolation**). With a slope near 1 this is harmless, which is one more
reason the slope is logged and worth a glance.

## Step 9 – The check star

One comp (the last in the ensemble) is designated the **check star**: it is
run through the same fit as if it were a target, and its measured magnitude
is compared with its catalog value.

*Why?* The check star is a canary. It is known to be constant, so if the
pipeline measures it far from its catalog value — much beyond the ensemble
RMS — something went wrong with this frame (clouds crossing, tracking error,
bad background), and the target measurement deserves suspicion. AAVSO
submissions include the check star (KNAME/KMF fields) so reviewers can apply
the same test. Because this check star was also *inside* the fit, it is a
consistency check rather than a fully independent one — a limitation worth
knowing about.

## Step 10 – The report

`format_aavso_report()` writes the observation as a single WebObs
Extended-format record, which is what the AAVSO accepts for upload:

| Field | Value | Why it's there |
|-------|-------|----------------|
| NAME | star name (spaces removed) | identifies the variable |
| DATE | Julian Date | when — the x-axis of the light curve |
| MAG / MERR | fitted magnitude / ensemble RMS | the measurement and its precision |
| FILT | filter band | magnitudes are only comparable within a band |
| TRANS | `NO` | no colour transformation was applied (see limitations) |
| MTYPE | `STD` | magnitude is on the standard catalog scale |
| CNAME / CMF | `ENSEMBLE` / `na` | signals ensemble method — no single comp star |
| KNAME / KMF | check-star AUID / catalog mag | lets reviewers verify the canary |
| AMASS | airmass | context for data quality assessment |
| CHART | VSP chart ID | traceability: exactly which comp sequence was used |
| NOTES | e.g. `Ensemble of 6 stars; RMS=0.0142` | method details |

## Tunable parameters (Analyze dialog)

| Parameter | Default | What it trades off |
|-----------|---------|--------------------|
| Comparison mag range | 11.0 – 13.5 | wider = more comps (better averaging) but risks saturated/noisy stars |
| Aperture radius | 6 px | larger = catches more starlight but admits more sky noise and neighbours |
| SNR threshold | 5.0 | lower = finds fainter stars but risks false detections |

## Assumptions and limitations

- **The image must be plate-solved** — star matching is entirely WCS-driven.
- **No colour transformation** (TRANS=NO). Different instruments have slightly
  different colour responses even through the same nominal filter; correcting
  for that requires observing standard fields and deriving transformation
  coefficients, which this pipeline does not apply. Measurements are therefore
  on the instrument's natural system, differenced against catalog values in
  the same band. (Per-telescope transformation coefficients can be recorded
  under **Edit → Telescopes…** for future use, but they do not yet enter the
  calculation.)
- **Fixed aperture, global background** — no per-star sky annulus and no PSF
  fitting; the smooth background model is assumed to represent the sky at
  every star.
- **Ensemble RMS as the error** — a scatter measure, not a full error
  propagation.
- **Stacked images are photometry-safe by construction**: VSTarget's stacks
  are registered NaN-aware *means* with no sigma clipping, a combination
  chosen deliberately because clipping and median-combining can distort star
  fluxes nonlinearly. The stack's reported JD comes from the reference frame's
  DATE-OBS.
