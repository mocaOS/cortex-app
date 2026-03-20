"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  HelpCircle,
  FileText,
  Code,
  Loader2,
  CheckCircle,
  AlertCircle,
  Sparkles,
  LucideIcon,
  Wand2,
  Search,
  Edit3,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import CollectionSelector from "@/components/CollectionSelector";
import { PageTransition } from "@/components/layout";
import type { CustomInputType, CustomInputItem } from "@/types";

const inputTypes: { type: CustomInputType; label: string; icon: LucideIcon; description: string }[] = [
  {
    type: "qa",
    label: "Q&A",
    icon: HelpCircle,
    description: "Add a question and answer pair",
  },
  {
    type: "text",
    label: "Text",
    icon: FileText,
    description: "Add plain text content",
  },
  {
    type: "markdown",
    label: "Markdown",
    icon: Code,
    description: "Add markdown formatted content",
  },
];

interface FormState {
  inputType: CustomInputType;
  content: string;
  answer: string;
  title: string;
  collectionId: string | undefined;
}

export default function AddPage() {
  const [form, setForm] = useState<FormState>({
    inputType: "qa",
    content: "",
    answer: "",
    title: "",
    collectionId: undefined,
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isGeneratingTopic, setIsGeneratingTopic] = useState(false);
  const [existingSimilar, setExistingSimilar] = useState<string[]>([]);
  const [submitResult, setSubmitResult] = useState<{
    success: boolean;
    message: string;
    filename?: string;
  } | null>(null);

  // Custom inputs list state
  const [customInputs, setCustomInputs] = useState<CustomInputItem[]>([]);
  const [isLoadingInputs, setIsLoadingInputs] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);

  // Fetch custom inputs
  const fetchCustomInputs = useCallback(async () => {
    try {
      const data = await api.getCustomInputs(searchQuery || undefined, 50);
      setCustomInputs(data.custom_inputs);
    } catch (error) {
      console.error("Failed to fetch custom inputs:", error);
    } finally {
      setIsLoadingInputs(false);
    }
  }, [searchQuery]);

  useEffect(() => {
    fetchCustomInputs();
  }, [fetchCustomInputs]);

  // Debounced search
  useEffect(() => {
    const timer = setTimeout(() => {
      fetchCustomInputs();
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery, fetchCustomInputs]);

  const handleGenerateTopic = async () => {
    if (!form.content.trim()) {
      setSubmitResult({
        success: false,
        message: "Please enter some content first to generate a topic hint",
      });
      return;
    }

    setIsGeneratingTopic(true);
    setExistingSimilar([]);

    try {
      const result = await api.generateTopicHint(
        form.content.trim(),
        form.inputType,
        form.inputType === "qa" ? form.answer.trim() : undefined
      );

      setForm({ ...form, title: result.topic_hint });
      setExistingSimilar(result.existing_similar || []);
    } catch (error) {
      setSubmitResult({
        success: false,
        message: error instanceof Error ? error.message : "Failed to generate topic hint",
      });
    } finally {
      setIsGeneratingTopic(false);
    }
  };

  const handleLoadForEdit = (item: CustomInputItem) => {
    setEditingId(item.id);
    setForm({
      inputType: item.input_type,
      content: item.content || "",
      answer: item.answer || "",
      title: item.topic_hint || "",
      collectionId: item.collection_id || undefined,
    });
    setSubmitResult(null);
    setExistingSimilar([]);
    // Scroll to form
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleCancelEdit = () => {
    setEditingId(null);
    setForm({
      inputType: "qa",
      content: "",
      answer: "",
      title: "",
      collectionId: undefined,
    });
    setSubmitResult(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!form.content.trim()) {
      setSubmitResult({
        success: false,
        message: "Please enter some content",
      });
      return;
    }

    if (form.inputType === "qa" && !form.answer.trim()) {
      setSubmitResult({
        success: false,
        message: "Please provide an answer for your Q&A",
      });
      return;
    }

    setIsSubmitting(true);
    setSubmitResult(null);

    try {
      // If editing, delete the old document first
      if (editingId) {
        await api.deleteDocument(editingId);
      }

      const result = await api.createCustomInput({
        input_type: form.inputType,
        content: form.content.trim(),
        answer: form.inputType === "qa" ? form.answer.trim() : undefined,
        title: form.title.trim() || undefined,
        collection_id: form.collectionId,
        start_processing: true,
      });

      setSubmitResult({
        success: true,
        message: editingId ? "Updated and reprocessing started" : result.message,
        filename: result.filename,
      });

      // Reset form after successful submission
      setForm({
        inputType: form.inputType,
        content: "",
        answer: "",
        title: "",
        collectionId: form.collectionId,
      });
      setEditingId(null);

      // Refresh the list
      fetchCustomInputs();
    } catch (error) {
      setSubmitResult({
        success: false,
        message: error instanceof Error ? error.message : "Failed to add content",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const selectedTypeInfo = inputTypes.find((t) => t.type === form.inputType);

  const getTypeIcon = (type: CustomInputType) => {
    const found = inputTypes.find((t) => t.type === type);
    return found ? found.icon : FileText;
  };

  return (
    <PageTransition>
      <div className="max-w-7xl mx-auto space-y-8">
        <div className="grid lg:grid-cols-2 gap-8">
          {/* Form */}
          <motion.form
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ delay: 0.3 }}
            onSubmit={handleSubmit}
            className="glass rounded-2xl p-6 space-y-6 relative overflow-visible"
          >
            {/* Editing indicator */}
            {editingId && (
              <div className="flex items-center justify-between p-3 rounded-xl bg-amber-500/10 border border-amber-500/20">
                <div className="flex items-center gap-2">
                  <Edit3 className="w-4 h-4 text-amber-400" />
                  <span className="text-sm text-amber-400">Editing existing entry</span>
                </div>
                <button
                  type="button"
                  onClick={handleCancelEdit}
                  className="p-1 rounded hover:bg-amber-500/20 text-amber-400"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}

            {/* Input Type Selector */}
            <div className="space-y-3">
              <label className="text-sm font-medium text-foreground">Content Type</label>
              <div className="grid grid-cols-3 gap-3">
                {inputTypes.map((type) => (
                  <button
                    key={type.type}
                    type="button"
                    onClick={() => setForm({ ...form, inputType: type.type })}
                    className={cn(
                      "flex flex-col items-center gap-2 p-4 rounded-xl border transition-all",
                      form.inputType === type.type
                        ? "border-accent bg-accent/10 text-accent"
                        : "border-border bg-card hover:border-muted-foreground text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <type.icon className="w-6 h-6" />
                    <span className="font-medium text-sm">{type.label}</span>
                  </button>
                ))}
              </div>
              {selectedTypeInfo && (
                <p className="text-xs text-muted-foreground">{selectedTypeInfo.description}</p>
              )}
            </div>

            {/* Collection Selector */}
            <div className="space-y-2 relative z-40">
              <label className="text-sm font-medium text-foreground">Collection</label>
              <CollectionSelector
                value={form.collectionId}
                onChange={(id) => setForm({ ...form, collectionId: id })}
                allowCreate={true}
                autoSelectDefault={true}
              />
            </div>

            {/* Content Area */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground">
                {form.inputType === "qa" ? "Question" : "Content"}
              </label>
              <textarea
                value={form.content}
                onChange={(e) => setForm({ ...form, content: e.target.value })}
                placeholder={
                  form.inputType === "qa"
                    ? "Enter your question here..."
                    : form.inputType === "markdown"
                    ? "Enter markdown content here..."
                    : "Enter your text content here..."
                }
                rows={form.inputType === "qa" ? 3 : 6}
                className="w-full px-4 py-3 bg-card border border-border rounded-xl text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent transition-colors resize-none font-mono text-sm"
              />
            </div>

            {/* Answer Field (only for Q&A) */}
            <AnimatePresence mode="wait">
              {form.inputType === "qa" && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="space-y-2 overflow-hidden"
                >
                  <label className="text-sm font-medium text-foreground">Answer</label>
                  <textarea
                    value={form.answer}
                    onChange={(e) => setForm({ ...form, answer: e.target.value })}
                    placeholder="Enter the answer to your question..."
                    rows={4}
                    className="w-full px-4 py-3 bg-card border border-border rounded-xl text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent transition-colors resize-none font-mono text-sm"
                  />
                </motion.div>
              )}
            </AnimatePresence>

            {/* Topic Hint with AI Generate Button */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-foreground flex items-center gap-2">
                Topic Hint
                <span className="text-xs text-muted-foreground font-normal">(optional)</span>
              </label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleGenerateTopic}
                  disabled={isGeneratingTopic || !form.content.trim()}
                  className={cn(
                    "flex items-center justify-center px-3 py-3 rounded-xl border transition-all shrink-0",
                    "border-accent/50 bg-accent/10 text-accent hover:bg-accent/20",
                    "disabled:opacity-50 disabled:cursor-not-allowed"
                  )}
                  title="Generate topic hint with AI"
                >
                  {isGeneratingTopic ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <Wand2 className="w-5 h-5" />
                  )}
                </button>
                <input
                  type="text"
                  value={form.title}
                  onChange={(e) => {
                    setForm({ ...form, title: e.target.value });
                    setExistingSimilar([]);
                  }}
                  placeholder="e.g., GraphRAG explanation..."
                  className="flex-1 px-4 py-3 bg-card border border-border rounded-xl text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent transition-colors"
                />
              </div>
              {/* Show existing similar topics */}
              <AnimatePresence>
                {existingSimilar.length > 0 && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="flex flex-wrap gap-2 pt-1">
                      <span className="text-xs text-muted-foreground">Similar:</span>
                      {existingSimilar.map((topic, i) => (
                        <button
                          key={i}
                          type="button"
                          onClick={() => {
                            setForm({ ...form, title: topic });
                            setExistingSimilar([]);
                          }}
                          className="text-xs px-2 py-1 rounded-md bg-muted hover:bg-muted/80 text-foreground transition-colors"
                        >
                          {topic}
                        </button>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Submit Result */}
            <AnimatePresence mode="wait">
              {submitResult && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className={cn(
                    "flex items-start gap-3 p-4 rounded-xl",
                    submitResult.success
                      ? "bg-green-500/10 border border-green-500/20"
                      : "bg-red-500/10 border border-red-500/20"
                  )}
                >
                  {submitResult.success ? (
                    <CheckCircle className="w-5 h-5 text-green-400 shrink-0 mt-0.5" />
                  ) : (
                    <AlertCircle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
                  )}
                  <div className="space-y-1">
                    <p
                      className={cn(
                        "text-sm font-medium",
                        submitResult.success ? "text-green-400" : "text-red-400"
                      )}
                    >
                      {submitResult.success ? "Success!" : "Error"}
                    </p>
                    <p className="text-sm text-muted-foreground">{submitResult.message}</p>
                    {submitResult.filename && (
                      <p className="text-xs text-muted-foreground">
                        Saved as: <code className="text-foreground">{submitResult.filename}</code>
                      </p>
                    )}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={isSubmitting || !form.content.trim()}
              className={cn(
                "w-full flex items-center justify-center gap-2 px-6 py-3 rounded-xl font-medium transition-all",
                editingId
                  ? "bg-amber-500 text-white hover:bg-amber-600"
                  : "bg-accent text-accent-foreground hover:bg-accent/90",
                "disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  {editingId ? "Updating..." : "Processing..."}
                </>
              ) : (
                <>
                  {editingId ? <Edit3 className="w-5 h-5" /> : <Sparkles className="w-5 h-5" />}
                  {editingId ? "Update Entry" : "Add to Knowledge Base"}
                </>
              )}
            </button>
          </motion.form>

          {/* Existing Custom Inputs List */}
          <motion.div
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ delay: 0.4 }}
            className="glass rounded-2xl p-6 space-y-4"
            style={{ zIndex: 1 }}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-medium text-foreground">Custom Entries</h3>
              <span className="text-xs text-muted-foreground">
                {customInputs.length} entries
              </span>
            </div>

            {/* Search */}
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search by content, topic, filename..."
                className="w-full pl-10 pr-4 py-2 bg-card border border-border rounded-xl text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent transition-colors"
              />
            </div>

            {/* List */}
            <div className="space-y-2 max-h-[500px] overflow-y-auto">
              {isLoadingInputs ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="w-6 h-6 text-accent animate-spin" />
                </div>
              ) : customInputs.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  {searchQuery ? "No matching entries found" : "No custom entries yet"}
                </div>
              ) : (
                customInputs.map((item) => {
                  const TypeIcon = getTypeIcon(item.input_type);
                  return (
                    <motion.div
                      key={item.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      className={cn(
                        "p-3 rounded-xl border transition-all cursor-pointer group",
                        editingId === item.id
                          ? "border-amber-500/50 bg-amber-500/10"
                          : "border-border bg-card/50 hover:border-muted-foreground hover:bg-card"
                      )}
                      onClick={() => handleLoadForEdit(item)}
                    >
                      <div className="flex items-start gap-3">
                        <div className="p-2 rounded-lg bg-muted shrink-0">
                          <TypeIcon className="w-4 h-4 text-muted-foreground" />
                        </div>
                        <div className="flex-1 min-w-0 space-y-1">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-medium text-accent uppercase">
                              {item.input_type}
                            </span>
                            {item.collection_name && (
                              <span className="text-xs text-muted-foreground">
                                in {item.collection_name}
                              </span>
                            )}
                          </div>
                          <p className="text-sm text-foreground line-clamp-2">
                            {item.topic_hint || item.content?.slice(0, 100)}
                          </p>
                          <p className="text-xs text-muted-foreground truncate">
                            {item.filename}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleLoadForEdit(item);
                          }}
                          className="p-2 rounded-lg opacity-0 group-hover:opacity-100 hover:bg-muted transition-all"
                        >
                          <Edit3 className="w-4 h-4 text-muted-foreground" />
                        </button>
                      </div>
                    </motion.div>
                  );
                })
              )}
            </div>

            {/* Edit hint */}
            <div className="pt-4 border-t border-border">
              <p className="text-xs text-muted-foreground">
                Click any entry to edit. Saving replaces the old entry and reprocesses the content.
              </p>
            </div>
          </motion.div>
        </div>

        {/* Tips Section */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="grid grid-cols-1 md:grid-cols-3 gap-4"
        >
          <div className="glass rounded-xl p-4 flex items-start gap-3">
            <div className="p-2 rounded-lg bg-accent/10 shrink-0">
              <HelpCircle className="w-4 h-4 text-accent" />
            </div>
            <div>
              <h4 className="text-sm font-medium text-foreground">Q&A Pairs</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Great for FAQs, definitions, and explanations
              </p>
            </div>
          </div>
          <div className="glass rounded-xl p-4 flex items-start gap-3">
            <div className="p-2 rounded-lg bg-accent/10 shrink-0">
              <FileText className="w-4 h-4 text-accent" />
            </div>
            <div>
              <h4 className="text-sm font-medium text-foreground">Text Content</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Works well for notes, summaries, and observations
              </p>
            </div>
          </div>
          <div className="glass rounded-xl p-4 flex items-start gap-3">
            <div className="p-2 rounded-lg bg-accent/10 shrink-0">
              <Code className="w-4 h-4 text-accent" />
            </div>
            <div>
              <h4 className="text-sm font-medium text-foreground">Markdown</h4>
              <p className="text-xs text-muted-foreground mt-1">
                Supports headings, lists, code blocks, and formatting
              </p>
            </div>
          </div>
        </motion.div>
      </div>
    </PageTransition>
  );
}
