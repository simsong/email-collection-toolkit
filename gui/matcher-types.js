/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";

class NameMatcherWindow extends MatcherWindow {
  constructor(root, groups, matcher = null) {
    super(root, groups, {title: "Name matcher", identityHeading: "Canonical name / email address",
      entryLabel: "canonical name", intro: "Collect alternative email addresses under a canonical name.", matcher});
  }
}

class InstitutionMatcherWindow extends MatcherWindow {
  constructor(root, groups, matcher = null) {
    super(root, groups, {title: "Institution matcher", identityHeading: "Institution / email address",
      entryLabel: "institution", intro: "Collect email addresses under their institution.",
      actionLabel: "Run institution matcher", matcher});
  }
}
