# E0659 — The camera ships 221 camera methods and advertises 47; the shutter is among the missing

Date: 2026-11-14. **Physical, read-only** on the owner's ILCE-6400 (firmware 2.00) over its own
access point, plus offline analysis of the packaged `orb-server`. No settings were written, no
mode changed, no photograph was taken by this record's commands.
Rebased onto upstream `main` on 2026-11-20 and numbered after the records published there; the measurements are unchanged.

Supersedes nothing. E0658 measured which methods this camera exposes; this record measures *why*
that list is short, and separates three things E0658 left together: what the firmware can do, what
the running application advertises, and what therefore answers.

## 0. Short answer

1. **The packaged firmware knows 221 camera methods.** `bin/orb-server`
   (SHA-256 `d65b995b2e9ff7c52da73a61603841a06e3010836ac64979ffcf5e2c7503de92`, 5,976,936
   bytes) contains the whole standardized method list — `actTakePicture`, `awaitTakePicture`,
   `actZoom`, `setFocusMode`, `setExposureMode`, `setStillSize`, `setStillQuality`,
   `setColorSetting`, `startMovieRec` and 212 more.
2. **The running application advertises 47.** `camera/getAvailableApiList` returns 47 names, and
   the set is stable across repeated `getEvent` cycles.
3. **The shutter is one of the 174 not advertised.** `actTakePicture` is absent from the 47 and
   answers error `1` (empty message) when sent. `actHalfPressShutter` is present and answers
   `200 {}`. The difference is state, not a missing firmware routine.
4. **The handler exists.** `WebApiUser::WebApi::wa_job_actTakePicture_cmd` is a real function in
   `libInfraWebApi.so` (SHA-256 `4677098775548c9b…`), confirmed by REA/Ghidra 12.1.4, and it does
   exactly one thing: `sendMsgToOrb` — hand the request to `orb-server`.
5. **Therefore the short list is the active application's registration, not a firmware limit.**
   The application running on this unit is `Smart Remote Control __SAK__` 2.1.7, read from
   `camera/getApplicationInfo`.

## 1. What works over the network on this body

Measured by call, not inferred from a list:

| capability | method | result |
| --- | --- | --- |
| live view | `startLiveview` **with no parameters** | URL, then MJPEG 640×424, ~38 KB/frame, 52 complete frames in 2,031,616 bytes |
| stop live view | `stopLiveview` | `200 [0]` |
| half-press | `actHalfPressShutter` | `200 {}` |
| release half-press | `cancelHalfPressShutter` | `200 {}` |
| continuous shooting | `startContShooting` / `stopContShooting` | `200 {}` both |
| read/write ISO, aperture, shutter, WB, EV | `get`/`setIsoSpeedRate` etc. | present in the 47 |
| shutter | `actTakePicture` | **error `1`**, and absent from the 47 |

`startLiveview` is worth stating precisely because it is a trap: called with a version string it
answers error `3` (illegal argument); called with nothing it returns the stream URL. That was
measured both ways.

## 2. The gap, counted two ways

`strings bin/orb-server` yields one line listing the standardized `service/method` pairs. Split on
the service prefix, it names **221** distinct `camera/` methods. The camera's own
`getAvailableApiList` names **47**. The difference is **174**, and a run-time set difference is
reproducible:

```bash
strings bin/orb-server | tr ':' '\n' | grep -o 'camera/[A-Za-z0-9_]*' | sort -u   # 221
python3 -I tools/research/camera_remote.py --offline \
  docs/evidence/cross_model/a6400-scalarwebapi-session.json                        # 47
```

The 174 include capabilities that would matter for the owner's work: `actZoom`,
`setFocusMode`, `setExposureMode`, `setStillSize`, `setStillQuality`, `setColorSetting`,
`setSceneSelection`, `startMovieRec`, `actTrackingFocus`, `setTouchAFPosition`.

## 3. Why this is not a firmware limitation

Three independent observations point the same way:

1. **The handler is compiled in.** `wa_job_actTakePicture_cmd` exists and runs; it forwards.
2. **The server knows the method.** `orb-server` contains `camera/actTakePicture` in its own
   dispatch list.
3. **The refusal is a state answer.** `actTakePicture` returns error `1`, documented by its own
   handler path as a state refusal, not error `12` (no such method). A method the camera did not
   know at all would be error `12`, as `getCreativeStyle` is.

So the shutter is unreachable *while this application is the one registered*, and the reason
lives in the application, which is not in the packaged firmware image: no `__SAK__` directory and
no `.apk` exist anywhere in the unpacked tree, so the application is installed on a partition the
updater image does not carry.

## 4. Alternative explanations

- **"Error 1 means the camera is busy; wait and retry."** Retried across more than ten event
  cycles and after `stopLiveview`; the answer stayed error `1` and the method stayed absent from
  the 47. A transient state would have changed at least once. The same code is what Sony's own
  client calls "Any" — an unclassified refusal — so nothing about the number itself says "busy";
  `Illegal Request` is `5` and `No Such Method` is `12` (E0660).
- **"`startRecMode` would widen the list."** It answers error `12` on this body, so the method is
  not present at all, and `getAvailableShootMode` returns only `still`. `setShootMode("movie")`
  answers error `1`. The camera is locked to still capture under this application.
- **"No memory card, so the shutter refuses."** `getStorageInformation` answered `200 [[]]` for
  the whole session, so no card was present. **This confound is not eliminated**, but the
  evidence points against it: `startContShooting` answered `200 {}` in the same state, and a
  body that refused to expose the shutter for lack of storage would not accept a continuous
  shooting request either. Both facts are true and neither settles it, so the limitation stands
  and the deciding call is still the same one with a card inserted.
- **"The owner's unit differs from the packaged firmware."** The unit reports firmware 2.00
  (`DigitalImagingDesc.xml`, `X_FirmwareVersion`), while the packaged updater analysed here is a
  different point release. Addresses quoted are the packaged image's, as the project requires.

## 5. Confidence and limitations

- **Verified on hardware:** the 47-method list and its stability, the live-view call shape and
  frame format, half-press and continuous shooting answering `200`, the shutter answering error
  `1`, `startRecMode` and `setShootMode` being refused, the empty storage list.
- **Verified offline:** 221 camera methods in `orb-server`; `wa_job_actTakePicture_cmd` existing
  and forwarding; the absence of the application package from the unpacked image.
- **Not verified:** whether a memory card makes the shutter answer differently — the storage list
  was empty and this is the one confound not eliminated. Whether a different on-camera
  application registers a wider list. Whether `movie` mode widens anything.
- **Not claimed:** that a modified client can force the shutter. Error `1` is a refusal by the
  application; attacking it is a separate question and is not attempted here.

## 6. Dependent implementation

`tools/research/scalarwebapi.py` is the transport and `tools/research/camera_remote.py` is the
remote built on it. The remote **discovers** its verbs from `getAvailableApiList` and refuses a
command the camera did not advertise, printing the reason, so a refusal is never reported as a
malfunction. Contract tests: `tests/test_scalarwebapi.py`, `tests/test_camera_remote.py`.

A recorded session is committed at
`docs/evidence/cross_model/a6400-scalarwebapi-session.json` so the remote can be exercised
`--offline` without a camera.

## 7. Next experiment

Insert a memory card and re-ask `actTakePicture` once, then `getStorageInformation`. If the
shutter still answers error `1` with storage present, the application registration is the whole
answer; if it starts working, this record's third claim is wrong and the storage check is the real
gate. Either outcome is decisive, and it needs one camera and one card.
