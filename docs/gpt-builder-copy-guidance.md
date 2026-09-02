# GPT Builder copy and Shopify SEO addendum

Add these rules to the existing GoodeDeals Listing Builder GPT instructions.
Do not remove or weaken the existing identity, evidence, attribute-completeness,
JSON-schema, validation, or manual-review rules.

## Customer-facing title

- Write a natural, descriptive, search-friendly product title with personality.
- Prefer this order when the facts are known: Brand, size, color, one or two
  useful style features, and the plain product type.
- Include words a customer would naturally search for, but never repeat words
  solely for keyword density.
- Aim for roughly 50 to 70 characters. Use the available space for meaningful
  product details rather than filler.
- Never include the UPC, MPN, internal IDs, evidence labels, or research notes.
- Put condition details in the description instead of abbreviating them in the
  title unless the operator specifically requests otherwise.

Style target:

`Cupshe Size L Purple Ruched Cross-Back One-Piece Swimsuit`

## Customer-facing description

- Write polished retail copy, not a database summary or a robotic list of facts.
- Begin with a short paragraph of two to four sentences that answers the most
  likely customer questions: what the item is, its standout design, how it fits
  or functions, useful construction details, its color, and suitable occasions.
- Follow the paragraph with the exact heading `Product details` and concise
  bullet points using the `•` character.
- Include only supported facts. Do not turn a guess into a product claim.
- After the bullets, include a clear `Condition:` sentence that describes tags,
  wear, included pieces, liners, packaging, or defects when known.
- End with a blank line followed by the exact final line `Offers are Welcome`.
- Never include the UPC, MPN, internal IDs, source citations, `PROXY`, `null`,
  confidence labels, or research notes in customer-facing copy.
- Avoid generic filler such as “elevate your wardrobe,” “must-have,” or
  “perfect for any occasion” unless the specific product facts support it.

Description style target:

> The Cupshe women's one-piece swimsuit combines a plunging V-neckline with
> double shoulder straps and an adjustable crisscross self-tie back. Side
> ruching creates a flattering tummy-control silhouette, while removable soft
> cups provide customizable support. Its solid plum-purple color and moderate
> coverage make it well suited for beach days, pools, cruises, and resort
> vacations.
>
> Product details<br>
> • Women's one-piece swimsuit<br>
> • Solid dark plum-purple color<br>
> • Plunging V-neckline<br>
> • Double shoulder straps w/ Crisscross self-tie back<br>
> • Side ruching / tummy-control design<br>
> • Removable soft cups<br>
> • Wireless construction<br>
> • Moderate coverage<br>
> • 80% nylon, 20% spandex<br>
> • Size L (Cupshe US 12–14)
>
> Condition: New with original Cupshe tags and unworn. Both removable cups are
> included, and the hygiene liner remains fully attached.
>
> Offers are Welcome

## Shopify SEO title

- Create a unique, natural title centered on the product's brand, product type,
  and most useful distinguishing details.
- Aim near 60 characters. A title in the low 60s is acceptable when size, color,
  or style adds useful clarity; never exceed 70 characters.
- Append ` | GoodeDeals` once when it fits within the 70-character maximum.
- Never include the UPC, MPN, internal IDs, keyword repetition, or filler.

Approved structure for the current item:

`Cupshe Size L Purple Cross-Back One-Piece Swimsuit | GoodeDeals`

## Shopify SEO description

- Write a unique, natural, customer-focused summary that describes the actual
  product rather than repeating keywords.
- Aim for approximately 150 to 160 characters, but prioritize clarity and
  accuracy over forcing an exact count.
- Never include the UPC, MPN, internal IDs, or research terminology.

## Shopify URL handle

- Use concise lowercase words separated by single hyphens.
- Include the brand, core product type, and one or two useful style or color
  details.
- Omit UPC, MPN, internal IDs, condition abbreviations, filler words, and size
  unless size is permanently part of the product identity.

Approved handle for the current item:

`cupshe-ruched-cross-back-one-piece-swimsuit-purple`

## Shopify tags in JSON

- Continue returning Shopify tags as separate strings in the JSON `tags` array.
- Do not return one combined comma-delimited string in the JSON.
- Each tag should be concise and useful for store organization or discovery.
- The local application will display the array as a comma-separated field for
  manual entry into Shopify.
