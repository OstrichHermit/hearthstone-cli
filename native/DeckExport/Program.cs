using System.Diagnostics;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;
using HearthMirror;
using HearthMirror.Objects;

namespace DeckExport;

internal static class Program
{
    private const string GameProcessName = "Hearthstone";

    private const int ExitOk = 0;
    private const int ExitNoProcess = 2;
    private const int ExitNoData = 3;

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private static int Main()
    {
        // All diagnostics go to stderr; stdout carries exactly one JSON line.
        Reflection.Exception += ex => Log($"HearthMirror exception: {ex.Message}");

        try
        {
            if (!IsGameRunning())
            {
                Emit(Status("no_process"));
                return ExitNoProcess;
            }

            List<Deck>? decks;
            try
            {
                decks = Reflection.Client.GetDecks();
            }
            catch (Exception ex)
            {
                Log($"GetDecks failed: {ex}");
                decks = null;
            }

            if (decks == null)
            {
                Emit(Status("no_data"));
                return ExitNoData;
            }

            Emit(BuildPayload(decks));
            return ExitOk;
        }
        catch (Exception ex)
        {
            Log($"Fatal: {ex}");
            Emit(Status("no_data"));
            return ExitNoData;
        }
    }

    private static bool IsGameRunning()
    {
        try
        {
            return Process.GetProcessesByName(GameProcessName).Length > 0;
        }
        catch (Exception ex)
        {
            Log($"Process lookup failed: {ex.Message}");
            return false;
        }
    }

    private static Payload BuildPayload(List<Deck> decks)
    {
        var payload = new Payload { Status = "ok" };
        foreach (var deck in decks ?? [])
        {
            if (deck is null)
            {
                continue;
            }

            var cards = new List<CardDto>();
            foreach (var card in deck.Cards ?? [])
            {
                if (card?.Id is null)
                {
                    continue;
                }

                cards.Add(new CardDto { Id = card.Id, Count = card.Count });
            }

            var sideboards = new SortedDictionary<string, List<CardDto>>(StringComparer.Ordinal);
            foreach (var (key, boardCards) in deck.Sideboards ?? [])
            {
                if (key is null || boardCards is null)
                {
                    continue;
                }

                var mapped = new List<CardDto>();
                foreach (var card in boardCards)
                {
                    if (card?.Id is null)
                    {
                        continue;
                    }

                    mapped.Add(new CardDto { Id = card.Id, Count = card.Count });
                }

                sideboards[key] = mapped;
            }

            payload.Decks.Add(new DeckDto
            {
                Id = deck.Id,
                Name = deck.Name ?? string.Empty,
                Hero = deck.Hero ?? string.Empty,
                HeroPower = deck.HeroPower ?? string.Empty,
                FormatType = deck.FormatType,
                Type = deck.Type,
                Cards = cards,
                Sideboards = sideboards,
            });
        }

        return payload;
    }

    private static Payload Status(string status) => new() { Status = status, Decks = null };

    // Writes a single UTF-8 line to stdout straight to the raw stream so that
    // console codepage settings cannot corrupt non-ASCII deck names.
    private static void Emit(Payload payload)
    {
        using var stdout = Console.OpenStandardOutput();
        JsonSerializer.Serialize(stdout, payload, JsonOptions);
        stdout.WriteByte((byte)'\n');
    }

    private static void Log(string message) => Console.Error.WriteLine($"[DeckExport] {message}");
}

internal sealed class Payload
{
    [JsonPropertyName("status")]
    public string Status { get; set; } = string.Empty;

    // Null (omitted) on non-ok statuses; always a (possibly empty) array on "ok".
    [JsonPropertyName("decks")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<DeckDto>? Decks { get; set; } = [];
}

internal sealed class DeckDto
{
    [JsonPropertyName("id")]
    public long Id { get; set; }

    [JsonPropertyName("name")]
    public string Name { get; set; } = string.Empty;

    [JsonPropertyName("hero")]
    public string Hero { get; set; } = string.Empty;

    [JsonPropertyName("heroPower")]
    public string HeroPower { get; set; } = string.Empty;

    [JsonPropertyName("formatType")]
    public int FormatType { get; set; }

    [JsonPropertyName("type")]
    public int Type { get; set; }

    [JsonPropertyName("cards")]
    public List<CardDto> Cards { get; set; } = [];

    [JsonPropertyName("sideboards")]
    public SortedDictionary<string, List<CardDto>> Sideboards { get; set; } = [];
}

internal sealed class CardDto
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = string.Empty;

    [JsonPropertyName("count")]
    public int Count { get; set; }
}
