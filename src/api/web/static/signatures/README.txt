Email-signature images (served publicly at /static/signatures/).

The 이혜람 / Hyeram Lee branded signatures reference these three files. Drop the
real images here (same filenames) and they appear in the compose preview and in
sent emails — no code change needed:

  hyeram-photo.jpg        # 프로필 사진 (104x104, 정사각형; 원형은 CSS가 처리)
  g2-users-love-us.png    # G2 "Users Love Us" 배지 (높이 68px 기준)
  perso-dubbing-logo.png  # Perso Dubbing 로고 (높이 22px 기준)

Until these files exist, the signature shows broken-image icons in those three
spots; all text/layout renders fine.

NOTE for sending real emails: email clients need ABSOLUTE image URLs. Set
PUBLIC_BASE_URL (e.g. https://<your-render-url>) so the send path can rewrite
/static/... to an absolute URL. In-app preview works with the relative path.
