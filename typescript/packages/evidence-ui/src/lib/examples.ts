// Example fields on the Start page: a known patch of sky with the profile that suits it, so a first
// run needs no coordinates. Each names its profile by id; it is offered only when that profile is.

export interface ExampleField {
  key: string;
  title: string;
  why: string;
  label: string;
  ra: string;
  dec: string;
  radius: string;
  profile: string;
}

export const EXAMPLES: ExampleField[] = [
  {
    key: "jwst-m92",
    title: "JWST: the outskirts of M92",
    why:
      "A globular cluster with public NIRCam images (programme 1334). About 50 Gaia stars fall on one detector, and the run checks each is where its motion puts it. About 40 MB, 10 to 20 minutes.",
    label: "M92 outskirts (JWST)",
    ra: "259.323",
    dec: "43.1505",
    radius: "0.004",
    profile: "jwst-image-check",
  },
];
